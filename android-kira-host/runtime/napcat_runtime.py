"""Install and configure official NapCat in the host's shared private Debian.

The caller serializes package operations and stops NapCat before calling ensure.
Only official installer bytes modify program files; the host owns configuration,
verified caches, attempt records and explicit backups. Backups are never restored
automatically, including after a cancelled or failed update.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import ssl
import stat
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import zipfile

from linux_installer import Artifact, InstallationCancelled, checkpoint, download
from runtime_paths import (NAPCAT_CODE, NAPCAT_DATA, NAPCAT_PENDING, NAPCAT_PROGRAM, NAPCAT_QQ,
                           RuntimePaths, root_path)
from initial_tokens import remember_initial_token
from storage_space import Allocation, REPORT, download_bytes, estimated_bytes, require_space, tree_size, zip_bytes
from runtime_cache import retire_attempt


INSTALLER_COMMIT = 'cbc0f10212a6a4d460b70861a5c8e2b6012119e5'
# NapNeko/NapCat-Installer is gone. This fork serves the same commit; the size and
# SHA256 below are unchanged, so only byte-identical installer content is accepted.
INSTALLER = Artifact('napcat-install-' + INSTALLER_COMMIT + '.sh',
    'https://raw.githubusercontent.com/xiwangly2/NapCat-Installer/' + INSTALLER_COMMIT + '/script/install.sh',
    '38fb655a218123b6e4759e40e9646fd1e8b637d82783e0cd183f5ea02240e1db', 43797)
# NapNeko/NapCatQQ no longer publishes release assets; the maintained fork below does.
# The earlier origin stays recognised only so a journalled attempt can finish from its verified cache.
_RELEASES = 'https://github.com/xxynet/NapCatQQ/releases/download/'
_FORMER_RELEASES = 'https://github.com/NapNeko/NapCatQQ/releases/download/'
_LATEST_RELEASE = 'https://api.github.com/repos/xxynet/NapCatQQ/releases/latest'
SHELL_VERSION = 'v4.9.43'
SHELL = Artifact('NapCat.Shell-' + SHELL_VERSION + '.zip',
    _RELEASES + SHELL_VERSION + '/NapCat.Shell.zip',
    '7ac86403a429de534a6843536a5c84cc69dfb61f3f6a33b1852cd5cad4d1148e', 35968066)
QQ_VERSION = '3.2.34-53644'
_QQ_BASE = 'https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_' + QQ_VERSION
# These are the exact Tencent archives used by the pinned official installer.
QQ_ARTIFACTS = {
    'arm64': Artifact('linuxqq_' + QQ_VERSION + '_arm64.deb', _QQ_BASE + '_arm64.deb',
        '181b895cb573548e396e54bb0548cca92ffdc1b1a4b018fa4551bb062385fdca', 207538924),
    'amd64': Artifact('linuxqq_' + QQ_VERSION + '_amd64.deb', _QQ_BASE + '_amd64.deb',
        '436c65e1dd28422e128a21cbddb5fe4e2e6c079d7f27fec2541e014d38cc336e', 187185496),
}
# Estimated 4 KiB allocation from each pinned package's data.tar inventory.
_QQ_EXTRACTED_BYTES = {
    '181b895cb573548e396e54bb0548cca92ffdc1b1a4b018fa4551bb062385fdca': 682213376,
    '436c65e1dd28422e128a21cbddb5fe4e2e6c079d7f27fec2541e014d38cc336e': 649416704,
}
_PACKAGES = ['zip', 'unzip', 'jq', 'curl', 'xvfb', 'screen', 'xauth', 'procps',
             'rpm2cpio', 'cpio', 'libnss3', 'libgbm1', 'libgssapi-krb5-2',
             'ca-certificates', 'libgcrypt20']
_DYNAMIC_PACKAGES = ['libglib2.0-0', 'libatk1.0-0', 'libatspi2.0-0',
                     'libgtk-3-0', 'libasound2']
_INSTALL = NAPCAT_PROGRAM[1:]
_QQ = NAPCAT_QQ[1:]
_NAPCAT = NAPCAT_CODE[1:]
_DATA = NAPCAT_DATA[1:]
_PENDING = NAPCAT_PENDING[1:]
_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')
_QQ_VERSION = re.compile(r'^(\d+)\.(\d+)\.(\d+)-(\d+)$')


def _path(root, relative):
    return root_path(root, '/' + relative)


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json(path, *, missing=None):
    path = Path(path)
    if not path.exists():
        return missing
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('NapCat configuration is not a bounded JSON file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('NapCat configuration must be a JSON object')
    return value


def _arch():
    value = platform.machine().lower()
    architecture = {'aarch64': 'arm64', 'arm64': 'arm64',
                    'x86_64': 'amd64', 'amd64': 'amd64'}.get(value)
    if architecture is None:
        raise ValueError('NapCat Linux QQ requires ARM64 or AMD64')
    return architecture


def _version_tuple(value, pattern=_VERSION):
    match = pattern.fullmatch(str(value))
    if not match:
        raise ValueError('Unrecognized official component version')
    return tuple(int(part) for part in match.groups())


def _napcat_version(code):
    # The release package.json is the monorepo version (0.0.1). The built
    # official bundle embeds the runtime version beside its development fallback.
    source = code / 'napcat.mjs'
    if not source.is_file() or not 0 < source.stat().st_size <= 16 * 1024 * 1024:
        return ''
    text = source.read_text(encoding='utf-8')
    match = re.search(r'''["'](\d+\.\d+\.\d+)["']\s*\|\|\s*["']1\.0\.0-dev["']''', text)
    return 'v' + match.group(1) if match else ''


def metadata(root):
    """Return actual installed versions without treating a marker as completion."""
    result = {'installed': False, 'version': '', 'qq_version': ''}
    try:
        qq = _path(root, _QQ)
        code = _path(root, _NAPCAT)
        package = _json(qq / 'resources/app/package.json', missing={})
        result['qq_version'] = str(package.get('version', ''))
        result['version'] = _napcat_version(code)
        native = 'arm64' if _arch() == 'arm64' else 'x64'
        required = [qq / 'qq', qq / 'resources/app/loadNapCat.js',
                    code / 'napcat.mjs', code / 'package.json', code / 'static/index.html',
                    code / ('native/packet/MoeHoo.linux.' + native + '.node'),
                    code / ('native/napi2native/napi2native.linux.' + native + '.node')]
        result['installed'] = (all(item.is_file() and item.stat().st_size > 0 for item in required)
            and package.get('main') == './loadNapCat.js'
            and bool(result['version'])
            and _version_tuple(result['qq_version'], _QQ_VERSION) >= _version_tuple(QQ_VERSION, _QQ_VERSION))
    except (OSError, ValueError, UnicodeError):
        pass
    return result


def installation_pending(root):
    try:
        path = _path(root, _PENDING)
        return path.exists() or path.is_symlink()
    except (OSError, RuntimeError, ValueError):
        return True


def has_legacy_data(root):
    """Preserve the legacy selection when a guest contains local QQ data."""
    try:
        paths = (_path(root, name) for name in (_INSTALL, _DATA))
        return any(not path.is_symlink() and path.is_dir() for path in paths)
    except (OSError, RuntimeError, ValueError):
        return False


def has_installation_files(root):
    """Cheap cold-start evidence. Startup still verifies complete metadata."""
    try:
        if installation_pending(root):
            return False
        files = (_QQ + '/qq', _QQ + '/resources/app/loadNapCat.js', _NAPCAT + '/napcat.mjs')
        for name in files:
            path = _path(root, name)
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def official_command():
    return ['/usr/bin/xvfb-run', '-a', NAPCAT_QQ + '/qq', '--no-sandbox']


def token_path(root):
    return _path(root, _NAPCAT + '/config/webui.json')


def _loopback(host):
    if not isinstance(host, str):
        return False
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_network(value):
    network = value.get('network', {})
    if not isinstance(network, dict):
        raise ValueError('NapCat OneBot network configuration is malformed')
    for kind in ('httpServers', 'websocketServers', 'httpClients', 'websocketClients'):
        rows = network.get(kind, [])
        if not isinstance(rows, list):
            raise ValueError('NapCat OneBot endpoints must be a list')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('enable', False)) is not bool:
                raise ValueError('NapCat OneBot endpoint is malformed')
            if row.get('enable') is not True:
                continue
            if kind.endswith('Servers'):
                local = _loopback(row.get('host'))
            else:
                address = urlsplit(str(row.get('url', '')))
                schemes = ('ws', 'wss') if kind == 'websocketClients' else ('http', 'https')
                local = (address.scheme in schemes and not address.username and not address.password
                         and _loopback(address.hostname))
            if not local:
                raise ValueError('NapCat has an enabled non-loopback OneBot endpoint; change it in the official panel before starting')
            if not isinstance(row.get('token'), str) or not row['token'].strip():
                raise ValueError('NapCat enabled OneBot endpoints require an authentication token')


def configure_local(root, *, initial_home=None, record_initial=False):
    """Use official configuration fields; never replace account or plugin data."""
    config = _path(root, _NAPCAT + '/config')
    config.mkdir(parents=True, exist_ok=True)
    # Validate every account before writing anything, preserving a rejected file.
    for path in sorted(config.glob('onebot11*.json')):
        _validate_network(_json(_path(root, path.relative_to(root).as_posix())))
    path = token_path(root)
    webui = _json(path, missing={})
    token = webui.get('token')
    if token is None or token == '':
        webui['token'] = secrets.token_urlsafe(32)
    elif not isinstance(token, str) or not token.strip():
        raise ValueError('NapCat WebUI token is malformed')
    desired = {**webui, 'host': '127.0.0.1', 'port': 6099}
    desired.setdefault('loginRate', 3)
    if desired != webui or not path.exists():
        _atomic(path, desired)
    if initial_home is not None:
        remember_initial_token(initial_home, 'napcat', desired.get('token') if record_initial else None)
    default = config / 'onebot11.json'
    if not default.exists():
        _atomic(default, {'network': {'httpServers': [], 'httpClients': [], 'websocketClients': [],
            'websocketServers': [{'name': 'KiraLocal', 'enable': True, 'host': '127.0.0.1',
                'port': 3001, 'messagePostFormat': 'array', 'reportSelfMessage': False,
                'token': secrets.token_urlsafe(32), 'debug': False, 'heartInterval': 30000}]},
            'musicSignUrl': '', 'enableLocalFile2Url': False, 'parseMultMsg': False})


def _latest_shell(cancel):
    checkpoint(cancel)
    request = Request(_LATEST_RELEASE,
        headers={'User-Agent': 'Kira-Android-Linux-Host', 'Accept': 'application/vnd.github+json'})
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    with urlopen(request, timeout=15, context=context) as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('NapCat release metadata redirected away from HTTPS')
        body = response.read(1024 * 1024 + 1)
    checkpoint(cancel)
    if len(body) > 1024 * 1024:
        raise ValueError('NapCat release metadata exceeds its limit')
    release = json.loads(body)
    tag = release.get('tag_name', '')
    _version_tuple(tag)
    assets = [item for item in release.get('assets', []) if item.get('name') == 'NapCat.Shell.zip']
    if len(assets) != 1 or release.get('prerelease') or release.get('draft'):
        raise ValueError('No unique stable official NapCat Shell release asset')
    asset = assets[0]
    url = _RELEASES + tag + '/NapCat.Shell.zip'
    if asset.get('browser_download_url') != url:
        raise ValueError('NapCat release asset does not belong to its official release')
    checksum = str(asset.get('digest', ''))
    if not checksum.startswith('sha256:'):
        raise ValueError('Official NapCat release does not supply a SHA256 digest')
    return tag, Artifact('NapCat.Shell-' + tag + '.zip', url, checksum[7:], asset['size'])


def _copy_metadata(target, info):
    # A destination's SELinux label belongs to its Android context. In
    # particular, copystat/copytree also copy xattrs and can fail before chmod.
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.chmod(target, stat.S_IMODE(info.st_mode))


def _copy(source, target, cancel):
    checkpoint(cancel)
    info = Path(source).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('NapCat copy source is not a regular file')
    with Path(source).open('rb') as input_stream, Path(target).open('xb') as output:
        while chunk := input_stream.read(1024 * 1024):
            checkpoint(cancel)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    checkpoint(cancel)
    _copy_metadata(target, info)
    checkpoint(cancel)
    return str(target)


def _copy_tree(source, target, cancel):
    checkpoint(cancel)
    source, target = Path(source), Path(target)
    info = source.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('NapCat backup source is not a directory')
    target.mkdir(mode=0o700)
    with os.scandir(source) as entries:
        for entry in entries:
            checkpoint(cancel)
            destination = target / entry.name
            child_info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(child_info.st_mode):
                directory_link = bool(getattr(child_info, 'st_file_attributes', 0)
                                      & getattr(stat, 'FILE_ATTRIBUTE_DIRECTORY', 0))
                os.symlink(os.readlink(entry.path), destination, target_is_directory=directory_link)
                if os.utime in os.supports_follow_symlinks:
                    os.utime(destination, ns=(child_info.st_atime_ns, child_info.st_mtime_ns),
                             follow_symlinks=False)
            elif stat.S_ISDIR(child_info.st_mode):
                _copy_tree(entry.path, destination, cancel)
            elif stat.S_ISREG(child_info.st_mode):
                _copy(entry.path, destination, cancel)
            else:
                raise ValueError('NapCat backup contains an unsupported special file')
    checkpoint(cancel)
    _copy_metadata(target, info)


def _backup(root, runtime, cancel):
    sources = [('program', _path(root, _INSTALL)), ('qq-data', _path(root, _DATA))]
    sources = [(name, path) for name, path in sources if path.exists()]
    if not sources:
        return None
    directory = runtime / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    record = {'state': 'copying', 'sources': [name for name, _ in sources]}
    _atomic(directory / 'backup.json', record)
    for name, source in sources:
        checkpoint(cancel)
        if source.is_symlink() or not source.is_dir():
            raise ValueError('NapCat backup source is not a private directory')
        _copy_tree(source, directory / name, cancel)
    checkpoint(cancel)
    _atomic(directory / 'backup.json', {**record, 'state': 'complete'})
    return directory


def _get(artifact, cache, cancel, publish):
    first_done = first_time = None
    last_done = None
    last_total = artifact.size
    speed = None
    def progress(done, total):
        nonlocal first_done, first_time, last_done, last_total, speed
        now = time.monotonic()
        if first_done is None:
            first_done, first_time = done, now
        last_done, last_total = done, total
        speed = max(0.0, (done - first_done) / max(0.001, now - first_time))
        percent = min(100.0, done * 100.0 / total) if total else None
        publish('napcat_download', '正在下载 ' + artifact.filename, None,
                transfer_active=total is None or done < total, transfer_kind='download',
                transfer_label=artifact.filename, transfer_done_bytes=done,
                transfer_total_bytes=total, transfer_percent=percent,
                transfer_bytes_per_second=speed, transfer_complete=bool(total and done == total))
    publish('napcat_download', '正在下载或校验 ' + artifact.filename, None,
            transfer_active=True, transfer_kind='download', transfer_label=artifact.filename,
            transfer_done_bytes=None, transfer_total_bytes=artifact.size, transfer_percent=None,
            transfer_bytes_per_second=None, transfer_complete=False)
    complete = False
    try:
        path = download(artifact, cache, cancel, progress, range_bytes=1024 * 1024)
        complete = True
        last_done, last_total = artifact.size, artifact.size
        return path
    finally:
        publish('napcat_download', '已校验 ' + artifact.filename if complete else '传输已停止：' + artifact.filename,
                None, transfer_active=False, transfer_kind='download', transfer_label=artifact.filename,
                transfer_done_bytes=last_done, transfer_total_bytes=last_total,
                transfer_percent=(min(100.0, last_done * 100.0 / last_total)
                                  if last_done is not None and last_total else None),
                transfer_bytes_per_second=speed, transfer_complete=complete)


def _guest(runtime, path):
    return RuntimePaths(runtime.parent.parent).as_guest_state(path)


def _dependencies(root, runtime, attempt, command, log, cancel, publish):
    marker = _path(root, '.napcat-host-packages.json')
    expected = {'installer': INSTALLER_COMMIT, 'base': _PACKAGES, 'dynamic': _DYNAMIC_PACKAGES}
    if _json(marker, missing={}) == expected:
        return
    publish('napcat_system_packages', '正在准备 Linux QQ、Xvfb 和系统依赖', None)
    command(['/usr/bin/dpkg', '--configure', '-a'], log)
    checkpoint(cancel)
    command(['/usr/bin/apt-get', '-o', 'APT::Status-Fd=3', 'update'], log)
    checkpoint(cancel)
    output = attempt / 'dynamic-packages.txt'
    script = ('set -eu; output=$1; shift; : > "$output"; for package in "$@"; do '
              'if /usr/bin/apt-cache show "${package}t64" >/dev/null 2>&1; '
              'then printf "%s\\n" "${package}t64"; else printf "%s\\n" "$package"; fi; '
              'done > "$output"')
    command(['/bin/bash', '-c', script, 'napcat-dependencies', _guest(runtime, output), *_DYNAMIC_PACKAGES], log)
    checkpoint(cancel)
    selected = output.read_text(encoding='utf-8').splitlines()
    if len(selected) != len(_DYNAMIC_PACKAGES) or any(
            choice not in (base, base + 't64') for choice, base in zip(selected, _DYNAMIC_PACKAGES)):
        raise ValueError('NapCat dependency selection did not match the official installer')
    command(['/usr/bin/apt-get', '-o', 'APT::Status-Fd=3', '-y', '--no-install-recommends',
             'install', *_PACKAGES, *selected], log)
    checkpoint(cancel)
    _atomic(marker, expected)


def _verify_shell(archive, root, cancel):
    code = _path(root, _NAPCAT)
    count = 0
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            checkpoint(cancel)
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('Official NapCat archive contains an unsafe member')
            if item.is_dir() or not path.parts or path.parts[0] in ('config', 'plugins'):
                continue
            target = _path(root, _NAPCAT + '/' + path.as_posix())
            if not target.is_file() or target.stat().st_size != item.file_size:
                raise ValueError('Official NapCat installation has an incomplete program file')
            with source.open(item) as expected, target.open('rb') as actual:
                while chunk := expected.read(128 * 1024):
                    checkpoint(cancel)
                    if actual.read(len(chunk)) != chunk:
                        raise ValueError('Installed NapCat program differs from the official release')
            count += 1
    if count == 0 or not code.is_dir():
        raise ValueError('Official NapCat release contains no program files')


def _install_space(root, runtime, artifacts, shell_archive, qq_artifact, cancel):
    program, qq_data, napcat_code = (_path(root, name) for name in (_INSTALL, _DATA, _NAPCAT))
    backup = tree_size(program, cancel)['copy_bytes'] + tree_size(qq_data, cancel)['copy_bytes']
    with zipfile.ZipFile(shell_archive) as archive:
        shell_bytes = zip_bytes(archive, cancel)
    qq_bytes = _QQ_EXTRACTED_BYTES[qq_artifact.sha256] if qq_artifact else 0
    old_shell = tree_size(napcat_code, cancel)['copy_bytes'] if qq_artifact else 0
    scratch = sum(estimated_bytes(artifact.size) for artifact in artifacts if artifact != qq_artifact)
    shared = [Allocation('napcat-backup', runtime / 'backups', backup),
              Allocation('napcat-archive-copies', runtime / 'attempts', scratch),
              Allocation('napcat-shell-extraction', runtime / 'attempts', shell_bytes),
              Allocation('napcat-qq-extraction', program, qq_bytes + old_shell)]
    qq_stage = shared + [Allocation('napcat-qq-archive-copy', runtime / 'attempts', estimated_bytes(qq_artifact.size))] if qq_artifact else shared
    shell_stage = shared + [Allocation('napcat-shell-installation', program, shell_bytes)]
    return qq_stage, shell_stage


def ensure(root, home, command, log, cancel, publish, updating=False):
    """Prepare NapCat using a serialized, cancellable caller-owned guest runner."""
    root, home = Path(root).resolve(), Path(home).resolve()
    checkpoint(cancel)
    before = metadata(root)
    first_configuration = not before['installed'] and not token_path(root).exists()
    pending_path = _path(root, _PENDING)
    pending = _json(pending_path)
    pending_exists = installation_pending(root)
    if updating and (not before['installed'] or pending_exists):
        raise ValueError('NapCat update requires a complete existing installation')
    if pending_exists and not pending:
        raise ValueError('NapCat has an unreadable pending installation record')
    if before['installed'] and not updating and not pending:
        configure_local(root, initial_home=home, record_initial=first_configuration)
        return before
    architecture = _arch()
    target_version, shell = _latest_shell(cancel) if updating else (SHELL_VERSION, SHELL)
    if pending:
        pending_version = pending['version']
        _version_tuple(pending_version)
        raw = pending['shell']
        expected_urls = [origin + pending_version + '/NapCat.Shell.zip' for origin in (_RELEASES, _FORMER_RELEASES)]
        if raw.get('url') not in expected_urls or raw.get('filename') != 'NapCat.Shell-' + pending_version + '.zip':
            raise ValueError('Pending NapCat attempt has no bound official release')
        if _version_tuple(pending_version) >= _version_tuple(target_version):
            target_version, shell = pending_version, Artifact(**raw)
        if before['version'] and _version_tuple(before['version']) > _version_tuple(target_version):
            raise ValueError('A pending NapCat installation conflicts with a newer program; preserving both for review')
    if before['installed'] and not pending and _version_tuple(before['version']) >= _version_tuple(target_version):
        configure_local(root, initial_home=home, record_initial=first_configuration)
        return before
    runtime = RuntimePaths(home).state / 'napcat'
    runtime.mkdir(parents=True, exist_ok=True)
    cache = runtime / 'downloads'
    report_path = runtime.parent / REPORT
    qq_version = before['qq_version']
    needs_qq = not qq_version or _version_tuple(qq_version, _QQ_VERSION) < _version_tuple(QQ_VERSION, _QQ_VERSION)
    qq_artifact = QQ_ARTIFACTS[architecture] if needs_qq else None
    artifacts = [INSTALLER, shell]
    if qq_artifact:
        artifacts.append(qq_artifact)
    downloads = sum(download_bytes(artifact, cache, cancel) for artifact in artifacts)
    require_space('napcat-download', [Allocation('download', cache, downloads)], cancel, report_path)
    attempt = runtime / 'attempts' / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    copies = {name: {'size': artifact.size, 'sha256': artifact.sha256}
              for name, artifact in [('install.sh', INSTALLER), ('NapCat.Shell.zip', shell), ('QQ.deb', qq_artifact)]
              if artifact is not None}
    record = {'state': 'preparing', 'version': target_version, 'installer': INSTALLER_COMMIT, 'copies': copies}
    _atomic(attempt / 'attempt.json', record)
    try:
        installer = _get(INSTALLER, cache, cancel, publish)
        shell_archive = _get(shell, cache, cancel, publish)
        qq_archive = _get(qq_artifact, cache, cancel, publish) if qq_artifact else None
        qq_stage, shell_stage = _install_space(root, runtime, artifacts, shell_archive, qq_artifact, cancel)
        require_space('napcat-install', qq_stage, cancel, report_path, alternative_phases=(shell_stage,))
        _dependencies(root, runtime, attempt, command, log, cancel, publish)
        require_space('napcat-install', qq_stage, cancel, report_path, alternative_phases=(shell_stage,))
        publish('napcat_backup', '正在备份 NapCat 程序、配置、插件和 QQ 数据', None)
        backup = _backup(root, runtime, cancel)
        record.update(state='installing', backup=str(backup) if backup else None)
        _atomic(attempt / 'attempt.json', record)
        for source, name in ((installer, 'install.sh'), (shell_archive, 'NapCat.Shell.zip'),
                             (qq_archive, 'QQ.deb')):
            if source is not None:
                _copy(source, attempt / name, cancel)
        checkpoint(cancel)
        _atomic(pending_path, {'version': target_version, 'shell': {
            'filename': shell.filename, 'url': shell.url, 'sha256': shell.sha256, 'size': shell.size},
            'attempt': attempt.name, 'backup': str(backup) if backup else None})
        publish('napcat_install', '正在运行官方 NapCat 安装器', None)
        guest_attempt = _guest(runtime, attempt)
        command(['/bin/bash', '-c', 'cd -- "$1" && exec /bin/bash ./install.sh --docker n --cli n --skip-deps --github-proxy 0',
                 'napcat-install', guest_attempt], log, timeout=1800)
        checkpoint(cancel)
        publish('napcat_verify', '正在核对官方 NapCat 完整程序及 Linux QQ 版本', None)
        _verify_shell(shell_archive, root, cancel)
        result = metadata(root)
        if not result['installed'] or result['version'] != target_version:
            raise ValueError('Official NapCat installer did not produce a complete matching installation')
        if before['qq_version'] and _version_tuple(result['qq_version'], _QQ_VERSION) < _version_tuple(before['qq_version'], _QQ_VERSION):
            raise ValueError('Linux QQ version decreased; preserved backup requires explicit user review')
        command(['/usr/bin/test', '-x', NAPCAT_QQ + '/qq'], log, timeout=30)
        # Settings kept by an explicit uninstall go back before local defaults are enforced.
        _restore_preserved(root, home, cancel)
        configure_local(root, initial_home=home, record_initial=first_configuration)
        checkpoint(cancel)
        _atomic(attempt / 'attempt.json', {**record, 'state': 'complete', **result})
        pending_path.unlink()
        try:
            retire_attempt(home, root, attempt, copies, cancel)
        except (OSError, ValueError, InstallationCancelled):
            # Installation is already committed. Keep unretired copies for a
            # later explicit cleanup instead of turning success into failure.
            pass
        publish('napcat_installed', '官方 NapCat 和 Linux QQ 已安装并校验', 100)
        return result
    except BaseException as error:
        state = 'cancelled' if isinstance(error, InstallationCancelled) else 'failed'
        _atomic(attempt / 'attempt.json', {**record, 'state': state, 'error': type(error).__name__})
        raise


# Explicit uninstall of the local NapCat program (plan R5C4).
#
# NapCat keeps its configuration and plugins inside the program tree, so they are copied
# out and verified before the program is removed, and copied back after a later fresh
# install. QQ login data stays unless the caller passes delete_qq_data. The program and
# QQ data are first renamed aside (the commit point) and only then deleted, so an
# interrupted uninstall is finished, never left half-deleted.
UNINSTALL_JOURNAL = 'uninstall.json'
UNINSTALL_SCHEMA = 'napcat-uninstall/v1'
PRESERVED = 'preserved'
_KEPT = ('config', 'plugins')
_STAGED = re.compile(r'preserved\.stage-[0-9a-f]{32}')
_TRASH = re.compile(r'\.napcat-uninstall-[0-9a-f]{32}')


class UninstallRefused(ValueError):
    """The uninstall did not start; its message is safe to show and nothing was changed."""


def _uninstall_dir(home):
    directory = RuntimePaths(home).state / 'napcat'
    if directory.is_symlink() or (os.path.lexists(directory) and not directory.is_dir()):
        raise UninstallRefused('NapCat 运行目录不是私有目录，已停止卸载')
    return directory


def _tree_digest(path, cancel):
    """Relative path -> (kind, sha256 or link target) for a tree, never following links."""
    result = {}
    base = Path(path)
    for folder, names, files in os.walk(base):
        checkpoint(cancel)
        for name in sorted(names + files):
            item = Path(folder) / name
            relative = item.relative_to(base).as_posix()
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ('link', os.readlink(item))
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ('dir', None)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with item.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        checkpoint(cancel)
                        digest.update(chunk)
                result[relative] = ('file', digest.hexdigest())
            else:
                raise ValueError('NapCat settings contain an unsupported special file')
        names[:] = [name for name in names if not (Path(folder) / name).is_symlink()]
    return result


def _remove(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif os.path.lexists(path):
        for folder, names, _ in os.walk(path):
            for name in names:
                child = Path(folder) / name
                if not child.is_symlink():
                    os.chmod(child, 0o700)
        os.chmod(path, 0o700)
        shutil.rmtree(path)


def _size(path):
    total = 0
    if not os.path.lexists(path) or Path(path).is_symlink():
        return 0
    for folder, names, files in os.walk(path):
        for name in files:
            try:
                total += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
    return total


def uninstall_summary(root, home):
    """What an uninstall would remove and keep, for the confirmation dialog."""
    program, code, data = _path(root, _INSTALL), _path(root, _NAPCAT), _path(root, _DATA)
    kept = sum(_size(code / name) for name in _KEPT)
    return {'installed_files': os.path.lexists(program), 'pending_installation': installation_pending(root),
            'program_bytes': max(0, _size(program) - kept), 'settings_bytes': kept, 'qq_data_bytes': _size(data),
            'preserved_waiting': os.path.lexists(_uninstall_dir(home) / PRESERVED)}


def uninstall(root, home, cancel, *, delete_qq_data=False):
    """Remove the local NapCat program after keeping its settings; the caller has stopped NapCat."""
    if type(delete_qq_data) is not bool:
        raise UninstallRefused('卸载选项无效')
    recover_uninstall(root, home)
    runtime = _uninstall_dir(home)
    program, code, data = _path(root, _INSTALL), _path(root, _NAPCAT), _path(root, _DATA)
    if installation_pending(root):
        raise UninstallRefused('NapCat 安装尚未完成或待恢复，请先完成安装再卸载')
    if not os.path.lexists(program) and not (delete_qq_data and os.path.lexists(data)):
        raise UninstallRefused('本机没有安装 NapCat')
    for path in (program, data, code):
        if path.is_symlink():
            raise UninstallRefused('NapCat 目录是符号链接，已停止卸载，未删除任何内容')
    runtime.mkdir(parents=True, exist_ok=True)
    preserved = runtime / PRESERVED
    if os.path.lexists(preserved):
        raise UninstallRefused('上次卸载保留的 NapCat 配置还没有恢复，请先重新安装本机 NapCat')
    for leftover in runtime.iterdir():
        if _STAGED.fullmatch(leftover.name):
            _remove(leftover)
    stage = runtime / ('preserved.stage-' + uuid.uuid4().hex)
    journal_path = runtime / UNINSTALL_JOURNAL
    journal = {'schema': UNINSTALL_SCHEMA, 'state': 'preserving', 'stage': stage.name,
               'delete_qq_data': delete_qq_data, 'started_at_unix_ms': int(time.time() * 1000)}
    _atomic(journal_path, journal)
    try:
        os.mkdir(stage, 0o700)
        kept = []
        for name in _KEPT:
            source = code / name
            if not os.path.lexists(source):
                continue
            if source.is_symlink() or not source.is_dir():
                raise UninstallRefused('NapCat 的 ' + name + ' 不是普通目录，已停止卸载，未删除任何内容')
            _copy_tree(source, stage / name, cancel)
            if _tree_digest(source, cancel) != _tree_digest(stage / name, cancel):
                raise ValueError('Preserved NapCat ' + name + ' differs from the installed copy')
            kept.append(name)
        meta = metadata(root)
        _atomic(stage / 'preserved.json', {'schema': 'napcat-preserved/v1', 'kept': kept,
                'version': meta.get('version'), 'qq_version': meta.get('qq_version'),
                'created_at_unix_ms': int(time.time() * 1000)})
        checkpoint(cancel)
    except BaseException:
        _remove(stage)
        _atomic(journal_path, {**journal, 'state': 'abandoned'})
        raise
    # Commit point: settings are safe outside the program, so removal goes ahead even if cancelled now.
    os.rename(stage, preserved)
    trash = {'program': '.napcat-uninstall-' + uuid.uuid4().hex}
    if delete_qq_data:
        trash['qq_data'] = '.napcat-uninstall-' + uuid.uuid4().hex
    journal = {**journal, 'state': 'deleting', 'trash': trash}
    _atomic(journal_path, journal)
    return _finish_uninstall(root, home, journal)


def _finish_uninstall(root, home, journal):
    program, data = _path(root, _INSTALL), _path(root, _DATA)
    targets = {'program': program, 'qq_data': data}
    removed = {}
    for kind, name in journal['trash'].items():
        if not _TRASH.fullmatch(name):
            raise ValueError('Uninstall journal names an unexpected directory')
        source = targets[kind]
        aside = source.parent / name
        if os.path.lexists(source) and not os.path.lexists(aside):
            if source.is_symlink():
                raise ValueError('NapCat directory became a symbolic link during uninstall')
            os.rename(source, aside)
        removed[kind] = _size(aside)
        _remove(aside)
    runtime = _uninstall_dir(home)
    result = {**journal, 'state': 'complete', 'removed_bytes': removed, 'finished_at_unix_ms': int(time.time() * 1000)}
    _atomic(runtime / UNINSTALL_JOURNAL, result)
    return result


def recover_uninstall(root, home):
    """Finish an uninstall a crash interrupted after its commit point, or discard one that never got there."""
    runtime = _uninstall_dir(home)
    journal_path = runtime / UNINSTALL_JOURNAL
    if journal_path.is_symlink():
        raise ValueError('NapCat uninstall journal is invalid; nothing was changed')
    try:
        journal = _json(journal_path)
    except (ValueError, UnicodeError):
        raise ValueError('NapCat uninstall journal is invalid; nothing was changed') from None
    if journal is None:
        return 'none'
    if journal.get('schema') != UNINSTALL_SCHEMA:
        raise ValueError('NapCat uninstall journal is invalid; nothing was changed')
    if journal.get('state') == 'preserving':
        stage = journal.get('stage')
        if not isinstance(stage, str) or not _STAGED.fullmatch(stage):
            raise ValueError('NapCat uninstall journal is invalid; nothing was changed')
        _remove(runtime / stage)
        _atomic(journal_path, {**journal, 'state': 'abandoned'})
        return 'abandoned'
    if journal.get('state') == 'deleting':
        if not isinstance(journal.get('trash'), dict) or not set(journal['trash']) <= {'program', 'qq_data'}:
            raise ValueError('NapCat uninstall journal is invalid; nothing was changed')
        _finish_uninstall(root, home, journal)
        return 'completed'
    return 'none'


def _restore_preserved(root, home, cancel):
    """Put settings kept by an uninstall back into a freshly installed program, then drop the kept copy."""
    preserved = _uninstall_dir(home) / PRESERVED
    if not os.path.lexists(preserved):
        return False
    if preserved.is_symlink() or not preserved.is_dir():
        raise ValueError('Preserved NapCat settings are not a private directory')
    code = _path(root, _NAPCAT)
    for name in _KEPT:
        source = preserved / name
        if not os.path.lexists(source):
            continue
        target = code / name
        if target.is_symlink():
            raise ValueError('Installed NapCat ' + name + ' is a symbolic link')
        # Kept files win over the installer's defaults; files only the installer created stay.
        expected = _tree_digest(source, cancel)
        for relative, (kind, value) in sorted(expected.items()):
            checkpoint(cancel)
            source_item, target_item = source / relative, target / relative
            if kind == 'dir':
                if os.path.lexists(target_item) and (target_item.is_symlink() or not target_item.is_dir()):
                    _remove(target_item)
                target_item.mkdir(parents=True, exist_ok=True)
                continue
            target_item.parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(target_item):
                _remove(target_item)
            if kind == 'link':
                os.symlink(value, target_item)
            else:
                _copy(source_item, target_item, cancel)
        actual = _tree_digest(target, cancel)
        if any(actual.get(relative) != entry for relative, entry in expected.items()):
            raise ValueError('Restored NapCat ' + name + ' does not match the kept copy')
    _remove(preserved)
    return True
