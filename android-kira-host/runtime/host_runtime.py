"""Provide a private Linux environment for the unmodified official Kira release.

The APK seeds source once. Official Kira owns subsequent source updates and its
supervisor owns child restarts. User data stays in the existing kira-core path.
"""
from concurrent.futures import Future
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
import uuid
import zipfile
from initial_tokens import remember_initial_token

from linux_installer import Artifact, InstallationCancelled, checkpoint, digest, download, unpack_rootfs
from dependency_progress import DependencyProgress, ProgressLogTail
from runtime_environment import load_environment
from migration_preflight import report as migration_report
from runtime_paths import (BASE_BINDING, HOST_STATE, KIRA_DATA, KIRA_PROGRAM, KIRA_REQUIREMENTS, KIRA_VENV,
                           PREFLIGHT_REPORT, ROOT_MARKER, BaseSelectionError, RuntimePaths, binding_value,
                           choose_base, generation_digest, private_path, readable_generations,
                           read_private_json, root_path)
from domestic_sources import APT_SOURCES, PIP_INDEX_URL, NPM_REGISTRY
from kira_backups import finalize_backup, recover_restore, restore_backup
from panel_health import HealthTracker, PROBE_INTERVAL_MS, boot_elapsed_ms, deadline_after, probe, request
from storage_space import (
    Allocation, REPORT as SPACE_REPORT, SpaceUnavailable, download_bytes,
    require_space, tar_bytes, tree_size, zip_bytes,
)
from runtime_intent import RECOVERY_DELAYS, STABLE_SECONDS, RuntimeIntent
from cleanup_recovery import CleanupUnconfirmed, verify_stopped
import runtime_cache
import retention

_cancel = threading.Event()
_run_lock = threading.Lock()
_process_lock = threading.RLock()
_stop_lock = threading.Lock()
_process = None
_home = None
_run_id = None
_state_lock = threading.Lock()
_PHASE = 'starting'
_timezone = 'UTC'
_capabilities = {}
_component_lock = threading.RLock()
_components = {}
_component_processes = {}
_component_busy = set()
# One bounded local request sequence per component, even across run handoffs.
_panel_jobs = {}
_unsafe_stop = {}
_desired = set()
_installs = set()
_updates = set()
_backups = set()
_restores = set()
_uninstalls = set()
_UNINSTALL_REQUEST = 'napcat-uninstall-request.json'
_RESTORE_REQUEST = 'restore-request.json'
_revisions = {}
_intent_store = None
_service_owner = None
_ANY_OWNER = object()
_recovery_due = {}
_active_component = None
_active_cancel = None
_manager_ready = False
_rootfs_name = None
_resolved_paths = None
_NODE_PROBE = ('node --version && '
    'node -e \'const fs=require("node:fs"), cp=require("node:child_process"); '
    'if(!fs.readFileSync("/etc/os-release","utf8")) process.exit(2); '
    'const child=cp.spawnSync(process.execPath,["-e","process.stdout.write(\\"kira-node-ok\\")"],{encoding:"utf8"}); '
    'if(child.status!==0 || child.stdout!=="kira-node-ok") process.exit(3)\' '
    '&& npm --version && npx --version')
_NATIVE_FILES = {'libproot.so', 'libprootloader.so', 'libtalloc.so', 'libandroid-shmem.so'}
_BOOTSTRAP_SHELL = ('printf \'{"pid":%s,"run_id":"%s"}\\n\' "$$" "$KIRA_LINUX_RUN_ID" '
                    '> "' + HOST_STATE + '/child-$KIRA_LINUX_RUN_ID.json"; exec "$@"')
_SYSTEM_PACKAGES = ['python3', 'python3-venv', 'python3-dev', 'build-essential',
                    'libstdc++6', 'ca-certificates', 'git']
_COMPONENTS = ('kira', 'napcat')


class RuntimeCompatibilityError(ValueError):
    """A documented host prerequisite is missing before downloading a guest."""


class RuntimeNetworkRequiredError(RuntimeError):
    """Bundled files are ready, but uncached system/Python packages need network."""


class RestoreFailed(ValueError):
    """A backup restore ended without replacing anything; its message is safe to show."""


class ProcessCleanupError(RuntimeError):
    """An owned command may still be alive; no subsequent spawn is safe."""


class _OperationCancel:
    def is_set(self):
        return _cancel.is_set() or (_active_cancel is not None and _active_cancel.is_set())

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set() and time.monotonic() < deadline:
            _cancel.wait(min(0.1, max(0, deadline - time.monotonic())))
        return self.is_set()


_operation_cancel = _OperationCancel()


def _component_snapshot():
    with _component_lock:
        snapshot = {key: dict(value, running=value['state'] == 'ready') for key, value in _components.items()}
        if _intent_store is not None:
            for name, component in snapshot.items():
                intent = _intent_store.values[name]
                component.update(wanted=intent['wanted'], intent_revision=intent['revision'],
                                 recovery_attempt=intent['attempts'], recovery_blocked=intent['blocked'])
        return snapshot


def _component_state(name, value, detail='', **extra):
    with _component_lock:
        _components[name].update(state=value, detail=detail, **extra)
        if value != 'ready':
            _components[name].pop('health', None)
        if value in ('stopped', 'error'):
            _components[name]['pid'] = None
            _components[name]['installing'] = False


def _publish_components(home, *, expected_owner=_ANY_OWNER):
    values = _component_snapshot()
    running = [name for name, value in values.items() if value['state'] == 'ready']
    preparing = any(value['state'] in ('starting', 'stopping', 'updating') for value in values.values())
    failed = any(value['state'] == 'error' for value in values.values())
    phase = 'starting' if preparing else 'ready' if running else 'error' if failed else 'stopped'
    detail = '、'.join(name.capitalize() for name in running) + ' 正在手机上运行' if running else '组件已停止'
    if preparing:
        detail = '正在准备组件；已启动的组件继续运行'
    elif failed:
        detail += '；有组件失败，请查看对应日志'
    state(home, phase, detail, 100 if not preparing else None, stage='components', _expected_owner=expected_owner)


def _normalize_timezone(value):
    """Translate Java fixed-offset IDs into POSIX TZ's opposite sign convention."""
    if not isinstance(value, str) or not 0 < len(value) <= 128:
        return 'UTC'
    offset = re.fullmatch(r'(?:GMT|UTC)([+-])(\d{1,2})(?::?(\d{2}))?(?::(\d{2}))?', value)
    if offset:
        sign, hours, minutes, seconds = offset.groups()
        hours, minutes, seconds = int(hours), int(minutes or 0), int(seconds or 0)
        if hours > 23 or minutes > 59 or seconds > 59:
            return 'UTC'
        if hours == minutes == seconds == 0:
            return 'UTC'
        result = 'UTC' + ('-' if sign == '+' else '+') + f'{hours:02d}:{minutes:02d}'
        return result + (f':{seconds:02d}' if seconds else '')
    if value.startswith(('GMT+', 'GMT-', 'UTC+', 'UTC-')):
        return 'UTC'
    if (re.fullmatch(r'[A-Za-z][A-Za-z0-9_+.-]*(?:/[A-Za-z0-9_+.-]+)*', value)
            and all(part not in ('.', '..') for part in value.split('/'))):
        return value
    return 'UTC'


def _android_timezone():
    try:
        from java.util import TimeZone
        value = str(TimeZone.getDefault().getID())
    except (ImportError, AttributeError, RuntimeError):
        value = os.environ.get('TZ', 'UTC')
    return _normalize_timezone(value)


def _node_manifest(manifest, architecture):
    abi = {'aarch64': 'arm64-v8a', 'arm64v8': 'arm64-v8a', 'amd64': 'x86_64'}.get(architecture, architecture)
    raw = manifest.get('abis', {}).get(abi, {}).get('nodeArtifact')
    if not isinstance(raw, dict):
        raise ValueError('Missing pinned Node LTS runtime artifact')
    artifact = Artifact(raw['filename'], raw['url'], raw['sha256'], raw['size'])
    architecture_name = 'arm64' if abi == 'arm64-v8a' else 'x64' if abi == 'x86_64' else None
    version = raw.get('version', '')
    if not architecture_name or not re.fullmatch(r'v\d+\.\d+\.\d+', version):
        raise ValueError('Invalid Node LTS version or architecture')
    topdir = 'node-' + version + '-linux-' + architecture_name
    if (raw.get('topdir') != topdir or raw.get('strip_components') != 1
            or artifact.filename != topdir + '.tar.xz'
            or artifact.url != 'https://nodejs.org/download/release/' + version + '/' + artifact.filename):
        raise ValueError('Node artifact does not match its official release archive')
    return artifact, raw


def _kernel_capabilities(minimum):
    """Record Node's documented baseline; older kernels require a real probe."""
    expected = re.fullmatch(r'(\d+)\.(\d+)', str(minimum))
    if expected is None:
        raise ValueError('Invalid documented Node kernel baseline')
    release = str(os.uname().release)[:128] if hasattr(os, 'uname') else ''
    actual = re.match(r'^(\d+)\.(\d+)', release)
    supported = tuple(map(int, actual.groups())) >= tuple(map(int, expected.groups())) if actual else None
    return {'kernel_release': release, 'node_official_kernel_baseline': str(minimum),
            'node_kernel_in_supported_range': supported, 'node_available': None,
            'node_error': None, 'node_checked': False}


def _publish_capabilities(home):
    _atomic_json(RuntimePaths(home).state / 'capabilities.json', _capabilities)


def _node_unavailable(home, error, *, checked=False):
    """Optional Node failure never authorizes an unchecked runtime binary."""
    _capabilities.update(node_available=False, node_checked=checked,
                         node_error=type(error).__name__ + ' at ' + _PHASE)
    _publish_capabilities(home)


def _node_warning():
    if _capabilities.get('node_available') is False:
        return 'Node 扩展暂不可用；Python 核心和 Python 插件可继续运行，详情见运行状态'
    if (_capabilities.get('node_available') is True
            and _capabilities.get('node_kernel_in_supported_range') is False):
        return '当前内核低于 Node 官方测试基线；Node 自检已通过，具体扩展仍需验证'
    return ''


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def state(home, value, detail='', progress=None, *, _expected_owner=_ANY_OWNER, **extra):
    """Publish only bounded, noncredential status and the owning Android PID."""
    result = {'state': value, 'pid': os.getpid(), 'backend': 'linux', 'timezone': _timezone, 'detail': detail, **extra}
    if _capabilities:
        result['capabilities'] = dict(_capabilities)
    warning = _node_warning()
    if warning:
        result['warnings'] = [warning]
    if progress is not None:
        result['progress'] = max(0, min(100, int(progress)))
    with _state_lock:
        if _expected_owner is not _ANY_OWNER:
            with _component_lock:
                if _expected_owner != _service_owner:
                    return
                result.update(components=_component_snapshot(), manager_ready=_manager_ready,
                              rootfs=_rootfs_name, paths=_resolved_paths)
                _atomic_json(RuntimePaths(home).state / 'status.json', result)
            return
        if _components:
            result.update(components=_component_snapshot(), manager_ready=_manager_ready,
                          rootfs=_rootfs_name, paths=_resolved_paths)
        _atomic_json(RuntimePaths(home).state / 'status.json', result)


def _download_progress(home, phase, detail, start, end):
    """Report transfer bytes independently from overall installation progress."""
    started = time.monotonic()
    initial_bytes = None
    last_time = -1.0
    last_percent = -1

    def report(done, total):
        nonlocal initial_bytes, last_time, last_percent
        done, total = max(0, int(done)), max(1, int(total))
        now = time.monotonic()
        if initial_bytes is None:
            initial_bytes = done
        fraction = min(1.0, done / total)
        percent = min(end, start + int(fraction * (end - start)))
        if percent == last_percent and now - last_time < 1 and done < total:
            return
        last_time, last_percent = now, percent
        speed = max(0.0, (done - initial_bytes) / max(0.001, now - started))
        state(home, 'starting', detail, percent, stage=phase,
              downloaded_bytes=done, total_bytes=total, bytes_per_second=round(speed, 2),
              download_percent=min(100, int(fraction * 100)),
              transfer_active=done < total, transfer_kind='download',
              transfer_label=detail, transfer_done_bytes=done, transfer_total_bytes=total,
              transfer_percent=round(fraction * 100, 2), transfer_bytes_per_second=round(speed, 2),
              transfer_complete=done == total)

    return report


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _verified_guests(home):
    return readable_generations(home)


def _component_list(value):
    if not isinstance(value, list) or any(name not in _COMPONENTS for name in value):
        raise ValueError('Invalid component selection')
    return list(dict.fromkeys(value))


def _read_component_selection(home):
    """Use the Java-owned persisted array, including an explicit empty selection."""
    if home is None:
        return ['kira']
    import napcat_runtime
    path = RuntimePaths(home).state / 'components.json'
    try:
        return _component_list(read_private_json(path, 1024))
    except FileNotFoundError:
        return list(_COMPONENTS) if any(napcat_runtime.has_legacy_data(root) for root in _verified_guests(home)) else ['kira']
    except (OSError, ValueError):
        return []


def _kira_installed(root):
    venv = root_path(root, KIRA_VENV)
    return ((venv / '.kira-requirements.json').is_file() and (venv / '.kira-venv.json').is_file()
            and ((venv / 'bin/python').is_file() or (venv / 'bin/python').is_symlink()))


def _abi_manifest(manifest, architecture):
    aliases = {'aarch64': 'arm64-v8a', 'arm64v8': 'arm64-v8a', 'amd64': 'x86_64'}
    abi = aliases.get(architecture, architecture)
    section = manifest.get('abis', {}).get(abi)
    if not isinstance(section, dict):
        raise ValueError('Unsupported Linux runtime architecture')
    raw = section['rootfsArtifact']
    artifact = Artifact(raw['filename'], raw['url'], raw['sha256'], raw['size'])
    hashes = section['nativeSHA']
    if set(hashes) != _NATIVE_FILES:
        raise ValueError('Runtime manifest native binary set is incomplete')
    if any(not isinstance(value, str) or len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value)
           for value in hashes.values()):
        raise ValueError('Runtime native binary digest is invalid')
    return artifact, hashes


def _stage_native(native_dir, runtime, expected):
    native_dir, runtime = Path(native_dir), Path(runtime)
    libraries = runtime / 'lib'
    libraries.mkdir(parents=True, exist_ok=True)
    for filename, checksum in expected.items():
        source = native_dir / filename
        if not source.is_file() or digest(source, _cancel) != checksum:
            raise ValueError('APK-native runtime binary failed integrity verification')
    for filename, target in [('libtalloc.so', 'libtalloc.so.2'), ('libandroid-shmem.so', 'libandroid-shmem.so')]:
        destination = libraries / target
        if not destination.exists() or digest(destination, _cancel) != expected[filename]:
            temporary = destination.with_name(destination.name + '.tmp')
            shutil.copyfile(native_dir / filename, temporary)
            os.replace(temporary, destination)
    temporary = runtime / 'tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    shared = runtime / 'shm'
    shared.mkdir(parents=True, exist_ok=True)
    os.chmod(shared, 0o700)
    environment = os.environ.copy()
    environment.update(LD_LIBRARY_PATH=str(libraries), PROOT_LOADER=str(native_dir / 'libprootloader.so'),
                       PROOT_TMP_DIR=str(temporary))
    environment.pop('PROOT_NO_SECCOMP', None)
    # Android's own Python loader paths must not leak into the guest Python.
    environment.pop('LD_PRELOAD', None)
    environment.pop('PROOT_L2S_DIR', None)
    return environment


def _remove_stage(stage, runtime):
    stage, runtime = Path(stage).resolve(), Path(runtime).resolve()
    if stage.parent != runtime or not stage.name.startswith('rootfs.stage-'):
        raise ValueError('Refusing cleanup outside the owned rootfs staging generation')
    if stage.exists():
        shutil.rmtree(stage)


def _prepare_rootfs(runtime, artifact, home):
    global _PHASE
    try:
        root = choose_base(home, artifact.sha256)
    except BaseSelectionError as error:
        _PHASE = 'migration_preflight'
        try:
            result = migration_report(home, artifact, error.reason, _cancel)
        except (OSError, ValueError, RuntimeError):
            result = {'version': 1, 'decision': 'blocked', 'reason': error.reason,
                      'requested_base_sha256': artifact.sha256, 'inventory_unavailable': True}
        try:
            checkpoint(_cancel)
            _atomic_json(runtime / PREFLIGHT_REPORT, result)
        except OSError:
            # Refusing the base switch does not depend on writing the inventory.
            pass
        raise
    if generation_digest(root) == artifact.sha256:
        try:
            runtime_cache.validate_base(root)
        except ValueError as error:
            raise RuntimeCompatibilityError(str(error)) from error
        _bind_base(runtime, home, root, artifact.sha256)
        return root
    cache = runtime / 'downloads'
    cached = (cache / artifact.filename).is_file()
    _PHASE = 'verify_rootfs' if cached else 'download_rootfs'
    detail = '正在校验并准备内置 Linux 基础环境' if cached else '正在下载并校验 Linux 基础环境；可停止后重试续传'
    state(home, 'starting', detail, 2, stage=_PHASE)
    progress = _download_progress(home, _PHASE, detail, 2, 25)
    require_space('rootfs-download', [Allocation('download', cache, download_bytes(artifact, cache, _cancel))],
                  _cancel, runtime / SPACE_REPORT)
    archive = download(artifact, cache, _cancel, progress)
    _PHASE = 'extract_rootfs'
    state(home, 'starting', '正在解包已校验的 Linux 环境', 27, stage=_PHASE)
    require_space('rootfs-extraction', [Allocation('rootfs-staging', runtime, tar_bytes(archive, _cancel))],
                  _cancel, runtime / SPACE_REPORT)
    stage = runtime / ('rootfs.stage-' + uuid.uuid4().hex)
    try:
        unpack_rootfs(archive, stage, _cancel)
        checkpoint(_cancel)
        _atomic_json(stage / ROOT_MARKER, {'sha256': artifact.sha256, 'version': 1})
        os.replace(stage, root)
    finally:
        _remove_stage(stage, runtime)
    _bind_base(runtime, home, root, artifact.sha256)
    return root


def _bind_base(runtime, home, root, identity):
    path = runtime / BASE_BINDING
    if not path.exists():
        _atomic_json(path, binding_value(home, root, identity))


def _prepare_node(runtime, root, artifact, metadata, home):
    """Stage only a SHA-pinned official Node distribution, preserving npm links."""
    global _PHASE
    opt = root / 'opt'
    opt.mkdir(parents=True, exist_ok=True)
    generation = opt / ('kira-node-' + artifact.sha256[:20])
    marker = generation / '.kira-node.json'
    if os.path.lexists(generation) and not runtime_cache._node_ready(root, {'sha256': artifact.sha256}):
        raise ValueError('Node 关键文件缺失或身份不匹配，需修复；现有数据已保留，KiraAI 可继续启动')
    if _read_json(marker, {}).get('sha256') != artifact.sha256:
        if generation.exists():
            raise ValueError('Existing Node runtime has no matching completion marker')
        cache = runtime / 'downloads'
        cached = (cache / artifact.filename).is_file()
        _PHASE = 'verify_node_archive' if cached else 'download_node'
        detail = '正在校验并准备内置 Node LTS' if cached else '正在下载并校验官方 Node LTS'
        state(home, 'starting', detail, 28, stage=_PHASE)
        progress = _download_progress(home, _PHASE, detail, 28, 35)
        require_space('node-download', [Allocation('download', cache, download_bytes(artifact, cache, _cancel))],
                      _cancel, runtime / SPACE_REPORT)
        archive = download(artifact, cache, _cancel, progress, range_bytes=1024 * 1024)
        _PHASE = 'extract_node'
        state(home, 'starting', '正在解包官方 Node、npm 与 npx', 36, stage=_PHASE)
        require_space('node-extraction', [Allocation('node-staging', opt, tar_bytes(archive, _cancel))],
                      _cancel, runtime / SPACE_REPORT)
        stage = opt / ('kira-node.stage-' + uuid.uuid4().hex)
        try:
            unpack_rootfs(archive, stage, _cancel, strip_components=metadata['strip_components'])
            for relative in ['bin/node', 'bin/npm', 'bin/npx', 'LICENSE']:
                if not (stage / relative).is_file():
                    raise ValueError('Official Node archive is incomplete')
            checkpoint(_cancel)
            _atomic_json(stage / '.kira-node.json', {'sha256': artifact.sha256, 'version': metadata['version']})
            os.replace(stage, generation)
        finally:
            # This exact private staging sibling contains no Kira user data.
            if stage.exists() and stage.parent.resolve() == opt.resolve() and stage.name.startswith('kira-node.stage-'):
                shutil.rmtree(stage)
    current = opt / 'kira-node'
    if current.exists() and not current.is_symlink():
        raise ValueError('Node runtime pointer is unexpectedly a directory')
    link = opt / ('kira-node.link-' + uuid.uuid4().hex)
    os.symlink(generation.name, link)
    os.replace(link, current)
    return generation


def _root_file(root, relative, content):
    target = private_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))


def _cached_guest_dns(root):
    """Read only bounded nameserver IPs from this private guest's prior config."""
    root = Path(root).resolve()
    target = root / 'etc/resolv.conf'
    try:
        if not target.resolve().is_relative_to(root) or target.stat().st_size > 65536:
            return []
        lines = target.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    addresses = []
    for line in lines:
        fields = line.partition('#')[0].split()
        if len(fields) != 2 or fields[0] != 'nameserver':
            continue
        try:
            address = str(ipaddress.ip_address(fields[1]))
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    return addresses[:4]


def _requirements_digest(code):
    requirements = private_path(code, 'requirements.txt').read_bytes()
    return hashlib.sha256(requirements).hexdigest()


def _bootstrap_requires_network(root, code):
    """Only uncached apt/pip steps require network; venv repair itself is local."""
    root = Path(root)
    return (_read_json(root / '.kira-host-packages.json', {}).get('packages') != _SYSTEM_PACKAGES
            or _read_json(private_path(code, 'venv/.kira-requirements.json'), {}).get('sha256') != _requirements_digest(code))


def _configure_guest(root, dns_servers, *, network_required=True):
    import certifi
    _root_file(root, 'etc/ssl/certs/ca-certificates.crt', Path(certifi.where()).read_bytes())
    addresses = []
    for value in dns_servers:
        if not isinstance(value, str):
            continue
        try:
            address = str(ipaddress.ip_address(value))
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        addresses = _cached_guest_dns(root)
    if not addresses and network_required:
        prepared = '内置 Linux 已准备完成' if _capabilities.get('node_available') is False else '内置 Linux 与 Node 已准备完成'
        raise RuntimeNetworkRequiredError(prepared + '；首次安装 Python 和核心依赖需要联网，请联网后再次启动。')
    _root_file(root, 'etc/resolv.conf', ''.join('nameserver ' + item + '\n' for item in addresses[:4]) + 'options timeout:3 attempts:2\n')
    _root_file(root, 'etc/hosts', '127.0.0.1 localhost\n::1 localhost ip6-localhost\n')
    (root / 'dev/shm').mkdir(parents=True, exist_ok=True)
    (root / 'run/kira-host').mkdir(parents=True, exist_ok=True)
    _root_file(root, 'etc/apt/sources.list', '')
    _root_file(root, 'etc/apt/sources.list.d/debian.sources', APT_SOURCES)
    _root_file(root, 'etc/apt/apt.conf.d/99kira',
               'Acquire::Retries "2";\nAcquire::https::Timeout "30";\n'
               'APT::Sandbox::User "root";\nDpkg::Use-Pty "0";\n')


def _extract_zip(archive, destination, allowed_roots=None):
    """Extract a bounded regular-file archive into a new private stage."""
    destination = Path(destination)
    size = 0
    seen = set()
    members = archive.infolist()
    if len(members) > 30000:
        raise ValueError('Bundled archive has too many members')
    for member in members:
        checkpoint(_cancel)
        name = member.filename
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name or '\x00' in name
                or not relative.parts or ':' in relative.parts[0]
                or (allowed_roots is not None and relative.parts[0] not in allowed_roots)):
            raise ValueError('Invalid bundled archive path')
        if relative.as_posix() in seen:
            raise ValueError('Duplicate bundled archive path')
        seen.add(relative.as_posix())
        mode = (member.external_attr >> 16) & 0o170000
        if mode not in (0, 0o100000, 0o040000):
            raise ValueError('Bundled archive cannot contain special files')
        size += member.file_size
        if size > 512 * 1024 * 1024:
            raise ValueError('Bundled archive exceeds extraction limits')
        target = destination / Path(*relative.parts)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('wb') as output:
                while chunk := source.read(128 * 1024):
                    checkpoint(_cancel)
                    output.write(chunk)
            os.chmod(target, 0o755 if member.external_attr >> 16 & 0o111 else 0o644)


def _verify_file(path, metadata, *, size_key='size', sha_key='sha256'):
    if (type(metadata.get(size_key)) is not int or metadata[size_key] <= 0
            or not re.fullmatch('[0-9a-f]{64}', str(metadata.get(sha_key, '')))
            or Path(path).stat().st_size != metadata[size_key]
            or digest(path, _cancel) != metadata[sha_key]):
        raise ValueError('Bundled official artifact failed integrity verification')


def _install_frontend(dist_zip, home, version):
    """Seed the exact official panel once, keeping the prior panel as a backup."""
    paths = RuntimePaths(home)
    data = paths.data
    data.mkdir(parents=True, exist_ok=True)
    stage = data / ('.kira-host-dist-stage-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        with zipfile.ZipFile(dist_zip) as archive:
            require_space('kira-panel', [Allocation('panel-staging', data, zip_bytes(archive, _cancel))],
                          _cancel, paths.state / SPACE_REPORT)
            _extract_zip(archive, stage)
        content = stage / 'dist' if (stage / 'dist/index.html').is_file() else stage
        if not (content / 'index.html').is_file() or not (content / 'assets').is_dir():
            raise ValueError('Official frontend archive is incomplete')
        (content / '.version').write_text(version, encoding='utf-8')
        target = data / 'dist'
        if target.exists():
            backup = private_path(paths.state, 'panel-backups/' + uuid.uuid4().hex)
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
        os.replace(content, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _stage_source(source_zip, root, home, manifest):
    """Install once, preserving the working directory owned by official updates."""
    source_zip, root, home = Path(source_zip), Path(root), Path(home)
    paths = RuntimePaths(home, root)
    code = paths.from_guest(KIRA_PROGRAM)
    record_path = paths.state / 'official-install.json'
    record = _read_json(record_path, {})
    required = ['main.py', 'requirements.txt', 'core/supervisor.py', 'core/launcher.py', 'webui/app.py']
    complete = code.is_dir() and all((code / item).is_file() for item in required)
    tools = paths.from_guest('/opt/kira-host')
    pip = manifest['pipArtifact']
    wheel = tools / 'wheels' / pip['filename']
    if record.get('state') == 'complete' and complete and wheel.is_file():
        try:
            _verify_file(wheel, pip)
            return code
        except (OSError, ValueError):
            # Reimport the tool wheel without overwriting the official program.
            pass
    if code.exists() and not complete:
        raise ValueError('Existing official Kira directory is incomplete; preserving it for recovery')
    repository = manifest['officialRepository']
    _verify_file(source_zip, repository, size_key='seed_size', sha_key='seed_sha256')
    opt = root / 'opt'
    opt.mkdir(parents=True, exist_ok=True)
    stage = opt / ('kira-host.stage-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        with zipfile.ZipFile(source_zip) as archive:
            require_space('kira-source', [Allocation('source-staging', opt, zip_bytes(archive, _cancel))],
                          _cancel, paths.state / SPACE_REPORT)
            _extract_zip(archive, stage, {'KiraAI', 'wheels', 'dist.zip'})
        staged_code = stage / 'KiraAI'
        if not all((staged_code / item).is_file() for item in required):
            raise ValueError('Bundled official source is incomplete')
        if (staged_code / 'venv').exists() or (staged_code / 'data').exists():
            raise ValueError('Official seed cannot contain runtime data or a virtual environment')
        staged_wheel = stage / 'wheels' / pip['filename']
        _verify_file(staged_wheel, pip)
        _verify_file(stage / 'dist.zip', manifest['frontendArtifact'])
        if not complete:
            record = {'state': 'pending', 'initial_commit': repository['initial_commit'],
                      'version': repository['version']}
            _atomic_json(record_path, record)
            checkpoint(_cancel)
            os.replace(staged_code, code)
        tools.mkdir(exist_ok=True)
        wheel.parent.mkdir(exist_ok=True)
        os.replace(staged_wheel, wheel)
        # A pending record can only come from this host's interrupted first seed.
        # Once complete, an APK upgrade never re-seeds the official panel/source.
        if record.get('state') == 'pending':
            _install_frontend(stage / 'dist.zip', home, repository['version'])
        _atomic_json(record_path, {**record, 'state': 'complete'})
        return code
    finally:
        if stage.exists() and stage.parent.resolve() == opt.resolve():
            shutil.rmtree(stage)


def _guest_argv(native_dir, root, home, command, run_id):
    node_available = _capabilities.get('node_available')
    node_path = '' if node_available is False else '/opt/kira-node/bin:'
    environment = {'HOME': '/root', 'USER': 'root', 'LOGNAME': 'root',
        'PATH': KIRA_VENV + '/bin:' + node_path + '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': _timezone, 'TMPDIR': '/tmp', 'PYTHONUNBUFFERED': '1',
        'DEBIAN_FRONTEND': 'noninteractive', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'PIP_INDEX_URL': PIP_INDEX_URL, 'npm_config_registry': NPM_REGISTRY,
        'KIRA_LINUX_RUN_ID': run_id,
        'KIRA_NODE_AVAILABLE': '0' if node_available is False else '1' if node_available is True else 'unknown',
        'KIRA_LINUX_HOST_PID': str(os.getpid())}
    environment.update(load_environment(home))
    bindings = [argument for guest, physical in RuntimePaths(home, root).bindings()
                for argument in ('-b', str(physical) + ':' + guest)]
    return [str(Path(native_dir) / 'libproot.so'), '--kill-on-exit', '--link2symlink', '-0', '-r', str(root),
            '-b', '/dev', '-b', '/proc', *bindings, '-w', KIRA_PROGRAM,
            '/usr/bin/env', '-i', *(key + '=' + value for key, value in environment.items()),
            '/bin/sh', '-c', _BOOTSTRAP_SHELL, 'kira-bootstrap', *map(str, command)]


class _ProcChild:
    def __init__(self, pid, birth):
        self.pid, self.birth = pid, birth

    def _signal(self, value):
        try:
            fields = Path('/proc', str(self.pid), 'stat').read_text().rpartition(')')[2].split()
            if fields[19] == self.birth:
                os.kill(self.pid, value)
        except (OSError, IndexError):
            pass

    def terminate(self): self._signal(signal.SIGTERM)
    def kill(self): self._signal(signal.SIGKILL)


def _owned_children(proc, run_id=None):
    # Chaquopy deliberately does not carry the Linux guest's psutil. Inspect
    # same-UID /proc entries, including children reparented by Electron/QQ.
    if Path('/proc').is_dir() and hasattr(os, 'getuid'):
        table, found = {}, set()
        for folder in Path('/proc').iterdir():
            if not folder.name.isdigit(): continue
            try:
                if folder.stat().st_uid != os.getuid(): continue
                fields = (folder / 'stat').read_text().rpartition(')')[2].split()
                if fields[0] == 'Z': continue
                pid = int(folder.name)
                if pid in (os.getpid(), proc.pid): continue
                table[pid] = (int(fields[1]), fields[19])
                if run_id and (b'KIRA_LINUX_RUN_ID=' + run_id.encode() + b'\0') in (folder / 'environ').read_bytes():
                    found.add(pid)
            except (OSError, ValueError, IndexError):
                continue
        parents = {proc.pid}
        while True:
            added = {pid for pid, (parent, _) in table.items() if parent in parents} - parents
            if not added: break
            parents.update(added)
        found.update(parents - {proc.pid})
        return [_ProcChild(pid, table[pid][1]) for pid in found if pid in table]
    try:
        import psutil
        return psutil.Process(proc.pid).children(recursive=True)
    except Exception:
        return []


def _terminate_owned(proc, home, run_id, timeout=8):
    """Signal the guest main process while its PRoot tracer can still reap it."""
    if proc is None:
        return
    children = _owned_children(proc, run_id)
    owned = {item.pid for item in children}
    marker = _read_json(RuntimePaths(home).state / ('child-' + run_id + '.json'), {})
    guest = marker.get('pid') if marker.get('run_id') == run_id else None
    if type(guest) is int and guest > 1 and guest != os.getpid():
        try:
            if guest in owned or (proc.poll() is None and os.getpgid(guest) == proc.pid):
                os.kill(guest, signal.SIGTERM)
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
        except ProcessLookupError:
            pass
    # Preserve the tracer until its tracees have received termination signals.
    for child in reversed(_owned_children(proc, run_id)):
        try: child.terminate()
        except Exception: pass
    try:
        if proc.poll() is not None:
            pass
        elif hasattr(os, 'killpg'):
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    for child in reversed(_owned_children(proc, run_id)):
        try: child.kill()
        except Exception: pass
    try:
        if proc.poll() is None:
            if hasattr(os, 'killpg'): os.killpg(proc.pid, signal.SIGKILL)
            else: proc.kill()
    except ProcessLookupError:
        pass
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    deadline = time.monotonic() + 2
    while _owned_children(proc, run_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _owned_children(proc, run_id):
        raise RuntimeError('Owned guest processes remain after stop; further starts are unsafe')
    marker_path = RuntimePaths(home).state / ('child-' + run_id + '.json')
    try: marker_path.unlink(missing_ok=True)
    except OSError: pass


def _spawn(native_dir, root, home, environment, command, log):
    global _process, _run_id
    checkpoint(_operation_cancel)
    run_id = uuid.uuid4().hex
    with _process_lock:
        checkpoint(_operation_cancel)
        if _process is not None:
            raise ProcessCleanupError('Previous command ownership has not been released')
        process = subprocess.Popen(_guest_argv(native_dir, root, home, command, run_id),
                                   cwd=str(home), env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        _process, _run_id = process, run_id
    return process, run_id


def _command(native_dir, root, home, environment, command, log, timeout=1800):
    tool = None
    if command[0] == '/usr/bin/apt-get':
        tool = 'apt'
    elif command[0] == '/usr/bin/dpkg':
        tool = 'install'
    elif '-m' in command and 'pip' in command and 'install' in command:
        tool = 'install' if '--no-index' in command else 'pip'
    parser = DependencyProgress(tool) if tool else None
    monitor = None
    baseline = _read_json(RuntimePaths(home).state / 'status.json', {})
    if not isinstance(baseline, dict):
        baseline = {}
    def publish(event):
        try:
            state(home, 'starting', baseline.get('detail', '正在准备运行依赖'),
                  baseline.get('progress'), stage=_PHASE, **event)
        except OSError:
            # Progress reporting must never skip process-group cleanup.
            pass
    if parser:
        publish(parser.snapshot())
        if isinstance(getattr(log, 'name', None), (str, os.PathLike)):
            try:
                log.flush()
                monitor = ProgressLogTail(log.name, log.tell(), parser)
            except OSError:
                pass
    def poll_progress():
        if monitor is not None:
            try:
                for event in monitor.poll():
                    publish(event)
            except OSError:
                pass
    launch = command
    if tool == 'apt':
        # APT marks its status descriptor close-on-exec before launching dpkg.
        # A dedicated duplicate keeps maintainer scripts' stdout/stderr open.
        apt = ['APT::Status-Fd=3' if argument == 'APT::Status-Fd=1' else argument for argument in command]
        launch = ['/bin/sh', '-c', 'exec 3>&1; exec "$@"', 'kira-apt-progress', *apt]
    process, run_id = _spawn(native_dir, root, home, environment, launch, log)
    deadline = time.monotonic() + timeout
    success = False
    try:
        while process.poll() is None:
            checkpoint(_operation_cancel)
            poll_progress()
            if time.monotonic() >= deadline:
                raise TimeoutError('Linux bootstrap step exceeded its deadline')
            _cancel.wait(0.2)
        poll_progress()
        if process.returncode:
            raise RuntimeError('Linux bootstrap command failed with exit code ' + str(process.returncode))
        success = True
    finally:
        if parser:
            publish(parser.finish(success))
        _cleanup_owned(process, home, run_id, log)


def _bootstrap(native_dir, root, home, environment, code, log, *, node_prepared=True):
    global _PHASE
    marker = root / '.kira-host-packages.json'
    if _read_json(marker, {}).get('packages') != _SYSTEM_PACKAGES:
        _PHASE = 'system_packages'
        state(home, 'starting', '首次安装 Linux Python 和系统依赖，请保持网络', 38, stage=_PHASE)
        _command(native_dir, root, home, environment, ['/usr/bin/apt-get', '-o', 'APT::Status-Fd=3', 'update'], log)
        _command(native_dir, root, home, environment, ['/usr/bin/dpkg', '--configure', '-a'], log)
        _command(native_dir, root, home, environment, ['/usr/bin/apt-get', '-o', 'APT::Status-Fd=3', '-y', '--no-install-recommends', 'install',
            *_SYSTEM_PACKAGES], log)
        _atomic_json(marker, {'packages': _SYSTEM_PACKAGES})
    if node_prepared:
        _PHASE = 'verify_node'
        state(home, 'starting', '正在实际检查 Node、npm、npx 和子进程能力', 54, stage=_PHASE)
        try:
            _command(native_dir, root, home, environment,
                     ['/bin/sh', '-c', _NODE_PROBE], log, timeout=90)
        except InstallationCancelled:
            raise
        except Exception as error:
            checkpoint(_cancel)
            _node_unavailable(home, error, checked=True)
        else:
            _capabilities.update(node_available=True, node_checked=True, node_error=None)
            _publish_capabilities(home)
    _PHASE = 'python_dependencies'
    required_digest = _requirements_digest(code)
    venv = private_path(code, 'venv')
    marker = venv / '.kira-requirements.json'
    venv_marker = venv / '.kira-venv.json'
    if (not venv_marker.is_file()
            or (not (venv / 'bin/python').exists() and not (venv / 'bin/python').is_symlink())):
        state(home, 'starting', '正在创建独立 Python 环境', 58, stage=_PHASE)
        _command(native_dir, root, home, environment, ['/usr/bin/python3', '-m', 'venv', KIRA_VENV], log)
        _atomic_json(venv_marker, {'version': 1})
    progress_pip = venv / '.kira-pip-progress.json'
    if _read_json(progress_pip, {}).get('version') != '26.3':
        state(home, 'starting', '正在离线安装依赖下载进度支持', 61, stage=_PHASE)
        _command(native_dir, root, home, environment, [KIRA_VENV + '/bin/python', '-m', 'pip', 'install',
            '--no-index', '--no-deps', '/opt/kira-host/wheels/pip-26.3-py3-none-any.whl'], log)
        _atomic_json(progress_pip, {'version': '26.3'})
    if _read_json(marker, {}).get('sha256') != required_digest:
        state(home, 'starting', '首次下载并安装 KiraAI Python 依赖；中断后可重试', 65, stage=_PHASE)
        _command(native_dir, root, home, environment, [KIRA_VENV + '/bin/python', '-m', 'pip', 'install',
            '--progress-bar=raw', '--timeout', '30', '--retries', '2',
            '-r', KIRA_REQUIREMENTS], log)
        _atomic_json(marker, {'sha256': required_digest})


def _ready(deadline=None):
    """Use public official endpoints without adding a marker to Kira source."""
    try:
        deadline = deadline_after() if deadline is None else deadline
        status, body = request(5267, '/api/health', deadline=deadline, max_bytes=4096)
        return (status == 200 and json.loads(body).get('status') == 'ok'
                and probe(5267, deadline=deadline))
    except Exception:
        return False


def _configure_webui(home):
    """Set supported listener configuration while preserving auth and user data."""
    paths = RuntimePaths(home)
    data = paths.data
    data.mkdir(parents=True, exist_ok=True)
    path = private_path(data, 'webui.json')
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('Existing WebUI configuration must be an object')
    else:
        config = {'access_token': secrets.token_urlsafe(32)}
    first_configuration = not path.exists()
    config.update(host='127.0.0.1', port=5267)
    _atomic_json(path, config)
    remember_initial_token(home, 'kira', config.get('access_token') if first_configuration else None)


def _official_command():
    return [KIRA_VENV + '/bin/python', '-u', KIRA_PROGRAM + '/main.py', '--data-dir', KIRA_DATA]


def _check_panel_port(port=5267):
    """Reject a live listener while allowing a recently closed TCP connection."""
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))


def _cleanup_owned(process, home, run_id, log):
    global _process
    try:
        _terminate_owned(process, home, run_id)
    except Exception as error:
        name = _active_component or 'environment'
        with _component_lock:
            _unsafe_stop[name] = type(error).__name__
            if name in _components:
                _component_processes.setdefault(name, {'process': process, 'run_id': run_id,
                    'log': log, 'revision': _revisions[name], 'deadline': 0, 'ready': False})
                _component_state(name, 'error', '无法确认进程已停止，已禁止再次启动',
                    error=type(error).__name__, unsafe_stop=True, run_id=run_id)
                _components[name]['pid'] = process.pid
            _persist_cleanup_blocker(name)
        raise ProcessCleanupError('Owned command cleanup is unresolved') from error
    # Termination can be slow; hold the lock only for the ownership handoff.
    # A cancelled, not-yet-registered spawn follows this path as well.
    with _process_lock:
        if _process is process:
            _process = None


def _component_owner(record):
    return None if record is None else (record, record['process'], record['run_id'], record['revision'])


def _matches_component_owner(record, owner):
    if owner is None:
        return record is None
    return (record is owner[0] and record['process'] is owner[1]
            and record['run_id'] == owner[2] and record['revision'] == owner[3])


def _manual_stop(home):
    return home is not None and (RuntimePaths(home).state / 'manual-stop').is_file()


def _cleanup_blocked():
    return (_intent_store is not None and
            any(value['blocked'] == 'cleanup_unconfirmed' for value in _intent_store.values.values()))


def _persist_cleanup_blocker(name):
    if _intent_store is None:
        return
    affected = (name,) if name in _COMPONENTS else _COMPONENTS
    try:
        _intent_store.commit({component: {'blocked': 'cleanup_unconfirmed'} for component in affected})
    except OSError:
        for component in affected:
            if component in _components:
                _components[component]['intent_persistence_failed'] = True


def _record_exit(name, owner, abnormal_exit):
    """Finalize an owned failure under the component lock, after guest cleanup."""
    if _revisions[name] != owner[3] or name not in _desired:
        return
    if _intent_store is None or _cancel.is_set() or _manual_stop(_home):
        _desired.discard(name)
        return
    intent = _intent_store.values[name]
    now = time.monotonic()
    ready_since = owner[0].get('ready_since', now)
    attempts = 0 if now - ready_since >= STABLE_SECONDS else intent['attempts']
    can_retry = abnormal_exit and owner[0]['ready'] and intent['wanted'] is True and not _unsafe_stop
    try:
        if can_retry and attempts < len(RECOVERY_DELAYS):
            _intent_store.commit({name: {'attempts': attempts + 1}})
            _recovery_due[name] = (owner[3], now + RECOVERY_DELAYS[attempts])
            _component_state(name, 'starting', '进程异常退出，将在短暂等待后重新启动', error=None)
            return
        blocked = 'retries_exhausted' if can_retry else 'outer_exit' if owner[0]['ready'] else 'startup_failed'
        _intent_store.commit({name: {'blocked': blocked}})
    except OSError:
        _component_state(name, 'error', '无法保存恢复状态，请检查存储后手动启动', error='IntentWriteError')
    _desired.discard(name)


def _stop_component(name, *, failure=None, claimed=False, expected=None, abnormal_exit=False):
    global _process
    with _component_lock:
        record = _component_processes.get(name)
        # Validate the observation and reserve stop ownership atomically. A stale
        # monitor cannot change the replacement's intent, busy flag or status.
        if expected is not None and (name in _unsafe_stop or not _matches_component_owner(record, expected)):
            return False
        if not claimed:
            if name in _component_busy: return False
            _component_busy.add(name)
        owner = _component_owner(record)
        if record:
            _component_state(name, 'stopping', '正在清理所属进程')
    try:
        if record:
            try:
                _terminate_owned(owner[1], _home, owner[2])
            except Exception as error:
                with _component_lock:
                    if _matches_component_owner(_component_processes.get(name), owner):
                        _unsafe_stop[name] = type(error).__name__
                        _component_state(name, 'error', '停止未能确认，已禁止再次启动；请保留日志人工检查',
                            error=type(error).__name__, unsafe_stop=True, run_id=owner[2])
                        _components[name]['pid'] = owner[1].pid
                        _desired.discard(name)
                        _persist_cleanup_blocker(name)
                return False
            if record['log'] is not None: record['log'].close()
        with _component_lock:
            if not _matches_component_owner(_component_processes.get(name), owner):
                return False
            if record:
                _component_processes.pop(name)
                _unsafe_stop.pop(name, None)
            _component_state(name, 'error' if failure else 'stopped', failure or '组件已停止', error=failure, unsafe_stop=False)
            if expected is not None and failure:
                _record_exit(name, owner, abnormal_exit)
        if record:
            with _process_lock:
                if _process is owner[1]: _process = None
        return True
    finally:
        with _component_lock: _component_busy.discard(name)


def control(component, action, owner_token=None):
    """Accept bridge commands without waiting for the serial installation worker."""
    if component not in (*_COMPONENTS, 'all') or action not in ('start', 'stop', 'restart', 'install', 'update', 'backup', 'restore', 'uninstall'):
        raise ValueError('Unsupported component action')
    if action in ('install', 'update') and component != 'napcat':
        raise ValueError('Only local NapCat uses this installation action')
    if action == 'backup' and component != 'kira':
        raise ValueError('NapCat update already includes its backup')
    if action == 'restore' and component != 'kira':
        raise ValueError('Only Kira backups can be restored')
    if action == 'uninstall' and component != 'napcat':
        raise ValueError('Only the local NapCat program can be uninstalled')
    if component == 'all' and action == 'stop':
        return stop(True, owner_token)
    with _component_lock:
        if owner_token != _service_owner: return False
        home = _home
    selection = _read_component_selection(home)
    names = selection if component == 'all' else (component,)
    claimed = []
    changed = False
    with _component_lock:
        if owner_token != _service_owner or not _manager_ready or _cancel.is_set(): return False
        if (_unsafe_stop or _cleanup_blocked()) and action != 'stop': return False
        for name, status in _components.items():
            selected = name in selection
            if status.get('selected') != selected:
                status['selected'] = selected
                changed = True
        accepted = []
        for name in names:
            installed = _components[name].get('installed', False)
            if action in ('start', 'restart', 'install') and name not in selection:
                continue
            if name == 'napcat':
                if action in ('start', 'restart', 'update') and not installed:
                    continue
                if action == 'install' and (installed or name in _installs or _active_component == name):
                    continue
            if action == 'start' and name in _desired and name not in _recovery_due: continue
            accepted.append(name)
        if _intent_store is not None and accepted:
            intent_changes = {name: {'revision': _revisions[name] + 1} for name in accepted}
            if action in ('start', 'restart', 'install', 'stop', 'uninstall'):
                for name, change in intent_changes.items():
                    blocked = _intent_store.values[name]['blocked']
                    change.update(wanted=action not in ('stop', 'uninstall'), attempts=0,
                                  blocked=blocked if blocked == 'cleanup_unconfirmed' else None)
            try:
                _intent_store.commit(intent_changes)
            except OSError:
                if action != 'stop': raise
                for name in accepted: _components[name]['intent_persistence_failed'] = True
        for name in accepted:
            changed = True
            _revisions[name] += 1
            _recovery_due.pop(name, None)
            _components[name].pop('health', None)
            if action in ('start', 'restart'): _desired.add(name)
            elif action == 'stop':
                _desired.discard(name)
                _installs.discard(name)
                _updates.discard(name)
                _backups.discard(name)
                _restores.discard(name)
                _uninstalls.discard(name)
                _components[name]['installing'] = False
            elif action == 'install':
                _installs.add(name)
                _desired.add(name)
                _component_state(name, 'starting', '正在准备安装本机 NapCat', installing=True,
                                 install_failed=False, error=None)
            elif action == 'update': _updates.add(name)
            elif action == 'backup': _backups.add(name)
            elif action == 'restore': _restores.add(name)
            elif action == 'uninstall':
                _desired.discard(name)
                _installs.discard(name)
                _updates.discard(name)
                _uninstalls.add(name)
            if _active_component == name and _active_cancel is not None:
                _active_cancel.set()
            if action in ('stop', 'restart', 'update', 'backup', 'restore', 'uninstall') and name not in _component_busy:
                _component_busy.add(name)
                claimed.append(name)
    for name in claimed: _stop_component(name, claimed=True)
    if changed and home is not None: _publish_components(home, expected_owner=owner_token)
    return True


def _kira_version(home, deadline=None):
    """Read the running official version through its authenticated local API."""
    try:
        deadline = deadline_after() if deadline is None else deadline
        path = private_path(RuntimePaths(home).data, 'webui.json')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return '未知'
            config_bytes = source.read(16_385)
        if len(config_bytes) > 16_384:
            return '未知'
        config = json.loads(config_bytes)
        status, body = request(5267, '/api/auth/login', method='POST',
            body=json.dumps({'access_token': config['access_token']}).encode(),
            headers={'Content-Type': 'application/json'}, deadline=deadline)
        if status != 200:
            return '未知'
        token = json.loads(body)['access_token']
        status, body = request(5267, '/api/version', headers={'Authorization': 'Bearer ' + token},
                               deadline=deadline)
        return str(json.loads(body)['version'])[:80] if status == 200 else '未知'
    except Exception:
        return '未知'


def _napcat_ready(deadline=None):
    return probe(6099, deadline=deadline)


def _observes_panel(name, owner):
    """Check from inside the component lock before dispatching or applying work."""
    return (owner is not None and _manager_ready and not _cancel.is_set() and name in _desired
            and name not in _component_busy and name not in _unsafe_stop
            and _revisions.get(name) == owner[3]
            and _matches_component_owner(_component_processes.get(name), owner)
            and owner[1].poll() is None)


def _start_panel_job(home, name, owner, kind):
    result = Future()
    deadline = deadline_after()

    def work():
        outcome = '未知' if kind == 'version' else False
        try:
            if kind == 'version':
                outcome = _kira_version(home, deadline)
            elif kind == 'startup':
                outcome = (_ready if name == 'kira' else _napcat_ready)(deadline)
            else:
                outcome = probe(5267 if name == 'kira' else 6099, deadline=deadline)
        except Exception:
            # A failed observation is evidence about the endpoint, not its process.
            pass
        finally:
            completed = (outcome, time.monotonic(), boot_elapsed_ms())
            with _component_lock:
                result.set_result(completed)
                if not _observes_panel(name, owner):
                    _panel_jobs.pop(name, None)

    _panel_jobs[name] = (owner, kind, result)
    thread = threading.Thread(target=work, name='kira-panel-' + name, daemon=True)
    try:
        thread.start()
    except RuntimeError:
        _panel_jobs.pop(name, None)


def _publish_panel_observations(home, names):
    """Merge facts into the current snapshot without replacing installation progress."""
    path = RuntimePaths(home).state / 'status.json'
    with _state_lock:
        saved = _read_json(path, {})
        if not isinstance(saved, dict) or saved.get('pid') != os.getpid():
            return
        components = saved.get('components')
        if not isinstance(components, dict):
            return
        with _component_lock:
            changed = False
            published = []
            for name in names:
                record = _component_processes.get(name)
                owner = _component_owner(record)
                stored = components.get(name)
                if (not _observes_panel(name, owner) or not record.get('observation_unpublished')
                        or not isinstance(stored, dict)
                        or stored.get('state') != 'ready'
                        or stored.get('run_id') != owner[2]):
                    continue
                for key in ('health', 'version'):
                    value = _components[name].get(key)
                    if stored.get(key) != value:
                        stored[key] = value
                        changed = True
                published.append(record)
            if changed:
                try:
                    _atomic_json(path, saved)
                except OSError:
                    return
            for record in published:
                record.pop('observation_unpublished', None)


def _observe_panels(home, boot_count):
    """Consume completed jobs and dispatch due work without waiting on any endpoint."""
    ready_unpublished = []
    observation_unpublished = set()
    with _component_lock:
        for name in ('kira', 'napcat'):
            record = _component_processes.get(name)
            owner = _component_owner(record)
            current = _observes_panel(name, owner)
            job = _panel_jobs.get(name)
            if job is not None and job[2].done():
                previous_owner, kind, result = _panel_jobs.pop(name)
                outcome, finished, observed_ms = result.result()
                if current and _matches_component_owner(record, previous_owner):
                    if kind == 'startup' and outcome and finished <= record['deadline']:
                        record['ready'] = True
                        record['ready_since'] = finished
                        version = {'version': '未知'} if name == 'kira' else {}
                        _component_state(name, 'ready', '官方面板已就绪', **version)
                        record['ready_unpublished'] = True
                    elif kind == 'version':
                        _components[name]['version'] = outcome
                        record['observation_unpublished'] = True
                    elif kind == 'health' and observed_ms is not None:
                        record['health_tracker'].record(outcome, observed_ms)

            if not current:
                continue
            available = name not in _panel_jobs
            if not record['ready']:
                if available and time.monotonic() < record['deadline']:
                    _start_panel_job(home, name, owner, 'startup')
                continue

            if record.get('ready_unpublished'):
                ready_unpublished.append(record)
            if 'health_tracker' not in record:
                record['health_tracker'] = HealthTracker()
            now = boot_elapsed_ms() if boot_count is not None else None
            health = record['health_tracker'].snapshot(now)
            health.update(boot_count=boot_count, pid=os.getpid(), run_id=owner[2])
            if _components[name].get('health') != health:
                _components[name]['health'] = health
                record['observation_unpublished'] = True
            if record.get('observation_unpublished'):
                observation_unpublished.add(name)
            if not available:
                continue
            if name == 'kira' and not record.get('version_requested'):
                _start_panel_job(home, name, owner, 'version')
                record['version_requested'] = name in _panel_jobs
            elif now is not None and now >= record.get('next_health_ms', 0):
                _start_panel_job(home, name, owner, 'health')
                record['next_health_ms'] = now + PROBE_INTERVAL_MS

    if ready_unpublished:
        try:
            _publish_components(home)
        except OSError:
            pass
        else:
            with _component_lock:
                for record in ready_unpublished:
                    record.pop('ready_unpublished', None)
    if observation_unpublished:
        _publish_panel_observations(home, observation_unpublished)


def _backup_kira(native_dir, root, home, environment, log):
    name = 'kira-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.tar'
    try:
        paths = RuntimePaths(home, root)
        folder = private_path(paths.state, 'backups')
    except ValueError:
        raise ValueError('Backup directory must remain inside private app files') from None
    folder.mkdir(parents=True, exist_ok=True)
    partial = name + '.partial'
    if folder.is_symlink() or not folder.resolve().is_relative_to(Path(home).resolve()):
        raise ValueError('Backup directory must remain inside private app files')
    if any(path.exists() or path.is_symlink() for path in (folder / partial, folder / name)):
        raise ValueError('Backup destination already exists')
    state(home, 'starting', '正在备份 Kira 程序、配置、插件和聊天数据', stage='kira_backup')
    program = tree_size(paths.from_guest(KIRA_PROGRAM), _operation_cancel, skip_top_level=('venv',))
    data = tree_size(paths.data, _operation_cancel)
    require_space('kira-backup', [Allocation('backup', folder, program['copy_bytes'] + data['copy_bytes'])],
                  _operation_cancel, paths.state / SPACE_REPORT)
    _command(native_dir, root, home, environment, ['/bin/tar', '-cf', paths.as_guest_state(folder / partial),
        '--exclude=' + KIRA_VENV[1:], '--exclude=__pycache__', '-C', '/', KIRA_PROGRAM[1:], KIRA_DATA[1:]], log)
    checkpoint(_operation_cancel)
    _, warnings = finalize_backup(folder, name, _operation_cancel)
    for warning in warnings:
        log.write(('Kira backup: ' + warning + '\n').encode('utf-8'))
    _component_state('kira', 'stopped', '备份已完成；旧备份清理未全部完成，请查看备份管理' if warnings
                     else '备份已验证，最多保留两份完整备份', backup='backups/' + name)


def _restore_kira(root, home, log):
    """Replace the stopped Kira program and data with the archive named by the Java-written request."""
    paths = RuntimePaths(home, root)
    request_path = paths.state / _RESTORE_REQUEST
    invalid = '恢复请求无效，请在备份管理里重新选择备份'
    try:
        request = read_private_json(request_path, 1024)
    except (OSError, ValueError):
        raise RestoreFailed(invalid) from None
    finally:
        # One request authorises one attempt; a failed or interrupted attempt needs a new confirmation.
        if not request_path.is_dir():
            request_path.unlink(missing_ok=True)
    filename, digest = (request.get('filename'), request.get('sha256')) if isinstance(request, dict) else (None, None)
    if (not isinstance(request, dict) or request.get('schema') != 'kira-restore-request/v1'
            or not isinstance(filename, str) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
        raise RestoreFailed(invalid)
    try:
        folder = private_path(paths.state, 'backups')
        archive = private_path(folder, filename)
    except ValueError:
        raise RestoreFailed(invalid) from None
    if folder.is_symlink() or archive.is_symlink() or not archive.is_file():
        raise RestoreFailed('所选备份已不存在，请在备份管理里重新选择')
    state(home, 'starting', '正在核验备份并恢复 KiraAI 程序、配置、插件和聊天数据', stage='kira_restore')
    work = private_path(paths.state, 'restore')
    work.mkdir(parents=True, exist_ok=True)
    # The replaced trees are moved aside, so the only extra space is the unpacked archive itself.
    require_space('kira-restore', [Allocation('restore', work, archive.stat().st_size)],
                  _operation_cancel, paths.state / SPACE_REPORT)
    try:
        result = restore_backup(home, folder, filename, digest, paths.from_guest(KIRA_PROGRAM), paths.data, _operation_cancel)
    except InstallationCancelled:
        raise
    except Exception as error:
        log.write(('Kira restore failed: ' + type(error).__name__ + ': ' + str(error) + '\n').encode('utf-8'))
        # Only an unfinished swap can leave the directories changed; the next start finishes or undoes it.
        swapping = _read_json(work / 'journal.json', {}).get('state') == 'swapping'
        raise RestoreFailed('恢复在切换目录时中断，下次启动会自动回滚或完成，详情见 kira.log' if swapping
                            else '恢复未完成，当前程序和数据没有改动，详情见 kira.log') from error
    log.write(('Kira restore: ' + filename + ' restored; previous program and data kept in ' + result['previous'] + '\n').encode('utf-8'))
    _component_state('kira', 'stopped', '已从备份恢复；恢复前的程序和数据保留了一份', installed=_kira_installed(root),
                     restored='backups/' + filename, version=None)


def _apply_retention(home, root):
    import napcat_runtime
    meta = napcat_runtime.metadata(root)
    if meta['installed']:
        qq = [item.filename for item in napcat_runtime.QQ_ARTIFACTS.values() if meta['qq_version'] in item.filename]
        current = ('NapCat.Shell-' + meta['version'] + '.zip', napcat_runtime.INSTALLER.filename, *qq)
    else:
        # Nothing installed: keep what a reinstall of the bundled baseline would download.
        current = (napcat_runtime.SHELL.filename, napcat_runtime.INSTALLER.filename,
                   *(item.filename for item in napcat_runtime.QQ_ARTIFACTS.values()))
    try:
        retention.apply(home, root, _cancel, napcat_current=current)
    except InstallationCancelled:
        raise
    except Exception as error:
        # Clean-up never turns a startup into a failure.
        try:
            _atomic_json(RuntimePaths(home).state / retention.REPORT,
                         {'version': 1, 'errors': [{'kind': 'all', 'error': type(error).__name__}]})
        except OSError:
            pass


def _uninstall_napcat(root, home, log):
    """Remove the stopped local NapCat program as the Java-written one-shot request specifies."""
    import napcat_runtime
    request_path = RuntimePaths(home, root).state / _UNINSTALL_REQUEST
    invalid = '卸载请求无效，请在菜单里重新选择卸载本机 NapCat'
    try:
        request = read_private_json(request_path, 1024)
    except (OSError, ValueError):
        raise napcat_runtime.UninstallRefused(invalid) from None
    finally:
        if not request_path.is_dir():
            request_path.unlink(missing_ok=True)
    if (not isinstance(request, dict) or request.get('schema') != 'napcat-uninstall-request/v1'
            or type(request.get('delete_qq_data')) is not bool):
        raise napcat_runtime.UninstallRefused(invalid)
    delete_qq_data = request['delete_qq_data']
    state(home, 'starting', '正在保留 NapCat 配置与插件并卸载本机 NapCat', stage='napcat_uninstall')
    result = napcat_runtime.uninstall(root, home, _operation_cancel, delete_qq_data=delete_qq_data)
    removed = sum(result.get('removed_bytes', {}).values())
    log.write(('NapCat uninstall: removed ' + str(removed) + ' bytes; QQ data '
               + ('deleted' if delete_qq_data else 'kept') + '\n').encode('utf-8'))
    _component_state('napcat', 'stopped', '本机 NapCat 已卸载，配置和插件已保留，重新安装时自动恢复；'
                     + ('QQ 登录数据已删除' if delete_qq_data else 'QQ 登录数据已保留'),
                     installed=False, installing=False, install_failed=False, version=None, qq_version=None, error=None)


def _start_component(name, native_dir, root, home, environment, code, *, node_prepared):
    global _active_component, _active_cancel, _process, _PHASE
    import napcat_runtime
    selection = _read_component_selection(home)
    with _component_lock:
        if _unsafe_stop or _cleanup_blocked() or name in _component_busy or _cancel.is_set():
            return
        retry = _recovery_due.get(name)
        if retry is not None and retry[0] == _revisions[name] and time.monotonic() < retry[1]:
            return
        _recovery_due.pop(name, None)
        if (name not in selection or _manual_stop(home)) and name in _desired:
            _desired.discard(name)
            _installs.discard(name)
            _component_state(name, 'stopped', '组件未请求运行', selected=name in selection)
        wanted = name in _desired
        installing = name in _installs
        updating = name in _updates
        backing_up = name in _backups
        restoring = name in _restores
        uninstalling = name in _uninstalls
        if not (wanted or updating or backing_up or restoring or uninstalling):
            return
        revision = _revisions[name]
        _active_component, _active_cancel = name, threading.Event()
        _installs.discard(name)
        _updates.discard(name)
        _backups.discard(name)
        _restores.discard(name)
        _uninstalls.discard(name)
    log_path = RuntimePaths(home).state / (name + '.log')
    try:
        retention.rotate_log(log_path)
    except OSError:
        pass
    log = log_path.open('ab', buffering=0)
    keep_log = False
    try:
        if uninstalling:
            _uninstall_napcat(root, home, log)
            return
        _component_state(name, 'updating' if updating else 'starting', '正在准备 ' + name.capitalize(), error=None)
        if backing_up:
            _backup_kira(native_dir, root, home, environment, log)
        if restoring:
            _restore_kira(root, home, log)
        if (backing_up or restoring) and not wanted: return
        if name == 'kira':
            _configure_webui(home)
            _bootstrap(native_dir, root, home, environment, code, log, node_prepared=node_prepared)
            _component_state(name, 'starting', 'KiraAI 环境已准备', installed=True)
            argv, port = _official_command(), 5267
        else:
            if installing or updating:
                def command(argv, stream, timeout=1800):
                    return _command(native_dir, root, home, environment, argv, stream, timeout=timeout)

                def publish(stage, detail, progress=None, **extra):
                    global _PHASE
                    _PHASE = stage
                    state(home, 'starting', detail, progress, stage=stage, **extra)

                metadata = napcat_runtime.ensure(root, home, command, log, _operation_cancel, publish, updating=updating)
            else:
                metadata = napcat_runtime.metadata(root)
                pending = napcat_runtime.installation_pending(root)
                if not metadata['installed'] or pending:
                    _component_state(name, 'error', '本机 NapCat 安装不完整，请手动安装', installed=False,
                                     install_failed=pending)
                    with _component_lock:
                        if _revisions[name] == revision: _desired.discard(name)
                    return
                napcat_runtime.configure_local(root)
            argv, port = napcat_runtime.official_command(), 6099
            _component_state(name, 'starting', 'NapCat 安装已验证', version=metadata.get('version'),
                qq_version=metadata.get('qq_version'), installed=True, installing=False, install_failed=False,
                config_path=napcat_runtime.token_path(root).relative_to(root).as_posix())
            installing = False
        checkpoint(_operation_cancel)
        with _component_lock:
            if _revisions[name] != revision: raise InstallationCancelled()
            wanted = name in _desired
        if not wanted:
            _component_state(name, 'stopped', '维护完成，组件保持停止')
            return
        _check_panel_port(port)
        process, run_id = _spawn(native_dir, root, home, environment, argv, log)
        with _component_lock:
            if _operation_cancel.is_set() or _revisions[name] != revision:
                _cleanup_owned(process, home, run_id, log)
                raise InstallationCancelled()
            _component_processes[name] = {'process': process, 'run_id': run_id, 'log': log,
                'revision': revision, 'deadline': time.monotonic() + 240, 'ready': False}
            _component_state(name, 'starting', '正在启动官方 ' + name.capitalize(), pid=process.pid, port=port)
            _components[name]['run_id'] = run_id
            keep_log = True
        with _process_lock:
            if _process is process: _process = None
    except InstallationCancelled:
        if name == 'napcat' and (installing or updating):
            pending = napcat_runtime.installation_pending(root)
            if pending:
                _component_state(name, 'stopped', 'NapCat 安装未完成，请手动继续', installed=False, installing=False)
            else:
                _component_state(name, 'stopped', '已取消本机 NapCat 安装', installing=False)
        elif restoring:
            _component_state(name, 'stopped', '已取消恢复，当前程序和数据没有改动')
        elif uninstalling:
            _component_state(name, 'stopped', '已取消卸载，本机 NapCat 没有改动')
        else:
            _component_state(name, 'stopped', '准备已取消，再次启动会继续')
    except Exception as error:
        log.write(('Host component failed: ' + type(error).__name__ + ': ' + str(error) + '\n').encode('utf-8'))
        if name not in _unsafe_stop:
            failed_installation = installing or (updating and napcat_runtime.installation_pending(root))
            installation = {'installed': False, 'installing': False, 'install_failed': True} if failed_installation else {}
            detail = (str(error) if isinstance(error, (SpaceUnavailable, RestoreFailed, napcat_runtime.UninstallRefused))
                      else '组件失败，请查看 ' + name + '.log')
            _component_state(name, 'error', detail, error=type(error).__name__, **installation)
        with _component_lock:
            if _revisions[name] == revision:
                _desired.discard(name)
                if (_intent_store is not None and _intent_store.values[name]['wanted'] is True
                        and not (installing or updating or backing_up or restoring or uninstalling) and not _cleanup_blocked()):
                    try:
                        _intent_store.commit({name: {'blocked': 'startup_failed'}})
                    except OSError:
                        _components[name]['intent_persistence_failed'] = True
    finally:
        if not keep_log and name not in _unsafe_stop: log.close()
        with _component_lock:
            _active_component, _active_cancel = None, None
        _publish_components(home)


def _monitor_components(home):
    with _component_lock:
        records = [(name, _component_owner(record)) for name, record in _component_processes.items()]
    for name, owner in records:
        record, process = owner[0], owner[1]
        if name in _unsafe_stop: continue
        failure = None
        returncode = process.poll()
        if returncode is not None:
            failure = '官方进程已退出 (' + str(process.returncode) + ')'
        elif not record['ready'] and time.monotonic() > record['deadline']:
            failure = '组件启动超时，请查看对应日志'
        if failure:
            stopped = _stop_component(name, failure=failure, expected=owner,
                                      abnormal_exit=returncode is not None and returncode != 0)
            with _component_lock:
                unresolved = name in _unsafe_stop and _matches_component_owner(_component_processes.get(name), owner)
            if stopped or unresolved:
                _publish_components(home)


def run(home, native_dir, assets_json, source_zip_path, architecture, dns_servers_json='[]', components_json=None,
        initial_actions_json='[]', boot_count=None, resume=False, owner_token=None):
    """Blocking Chaquopy bridge entry; the Android Service owns this worker."""
    global _intent_store, _service_owner
    if owner_token is not None and (not isinstance(owner_token, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,128}', owner_token)):
        raise ValueError('Invalid runtime service owner')
    if type(resume) is not bool:
        raise ValueError('Invalid runtime resume flag')
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError('A Linux backend is already running')
    try:
        return _run_locked(home, native_dir, assets_json, source_zip_path, architecture,
                           dns_servers_json, components_json, initial_actions_json, boot_count, resume, owner_token)
    finally:
        # An admitted stop may still be between cleanup steps. Keep the old
        # owner bound until that operation has left its serial cleanup lock.
        with _stop_lock, _component_lock:
            _intent_store = _service_owner = None
        # Lock ownership must outlive cleanup, but never depend on status I/O succeeding.
        _run_lock.release()


def _run_locked(home, native_dir, assets_json, source_zip_path, architecture,
                dns_servers_json, components_json, initial_actions_json, boot_count, resume, owner_token):
    global _home, _PHASE, _process, _timezone, _capabilities, _manager_ready, _rootfs_name, _resolved_paths
    global _intent_store, _service_owner
    import napcat_runtime
    if _unsafe_stop or _component_processes or _process is not None:
        raise RuntimeError('Previous component cleanup is unresolved; refusing another backend')
    boot_count = boot_count if type(boot_count) is int and boot_count >= 0 else None
    _capabilities = {}
    home = Path(home).resolve()
    _home = home
    home.mkdir(parents=True, exist_ok=True)
    runtime = RuntimePaths(home).state
    runtime.mkdir(parents=True, exist_ok=True)
    intent = RuntimeIntent(runtime / 'runtime-intent.json')
    with _component_lock:
        _intent_store = intent
        intent.consume_pending_stop()
        if _cleanup_blocked():
            raise RuntimeError('Previous component cleanup is unresolved; refusing another backend')
        _service_owner = owner_token
        _cancel.clear()
    monitor_stop = threading.Event()
    monitor = None
    try:
        # A restore swap that a crash interrupted is finished or undone before anything reads the program or data.
        _PHASE = 'restore_recovery'
        recover_restore(home)
        _PHASE = 'starting'
        selection = _read_component_selection(home)
        requested = json.loads(components_json) if isinstance(components_json, str) else components_json
        requested = selection if requested is None else _component_list(requested)
        requested = set(requested).intersection(selection)
        actions = json.loads(initial_actions_json) if isinstance(initial_actions_json, str) else initial_actions_json
        if not isinstance(actions, list) or any(not isinstance(item, dict) or
                (item.get('component'), item.get('action')) not in (('kira', 'backup'), ('kira', 'restore'), ('napcat', 'install'), ('napcat', 'update'), ('napcat', 'uninstall')) for item in actions):
            raise ValueError('Invalid initial maintenance actions')
        guests = tuple(_verified_guests(home))
        installed = {'kira': any(_kira_installed(root) for root in guests),
                     'napcat': any(napcat_runtime.has_installation_files(root) for root in guests)}
        napcat_pending = not installed['napcat'] and any(napcat_runtime.installation_pending(root) for root in guests)
        install_napcat = ('napcat' in selection and not installed['napcat']
                          and any(item['action'] == 'install' for item in actions))
        if install_napcat:
            requested.add('napcat')
        elif not installed['napcat']:
            requested.discard('napcat')
        with _component_lock:
            checkpoint(_cancel)
            intent.consume_pending_stop()
            blocked = set()
            if _manual_stop(home):
                requested.clear()
                install_napcat = False
            if resume:
                blocked = {name for name in requested if intent.values[name]['wanted'] is not False
                           and intent.values[name]['blocked'] is not None}
                requested = {name for name in requested - blocked if intent.values[name]['wanted'] is not False}
            intent.commit({name: {'wanted': True, 'revision': intent.values[name]['revision'] + 1,
                                  'attempts': 0, 'blocked': None}
                           for name in requested if not resume or intent.values[name]['wanted'] is None})
            _components.clear()
            _component_processes.clear()
            _component_busy.clear()
            _desired.clear()
            _desired.update(requested)
            _installs.clear()
            if install_napcat: _installs.add('napcat')
            _updates.clear()
            _backups.clear()
            _updates.update(item['component'] for item in actions if item['action'] == 'update' and installed['napcat'])
            _backups.update(item['component'] for item in actions if item['action'] == 'backup')
            _restores.clear()
            _restores.update(item['component'] for item in actions if item['action'] == 'restore')
            _uninstalls.clear()
            _uninstalls.update(item['component'] for item in actions if item['action'] == 'uninstall')
            _revisions.clear()
            _revisions.update({name: value['revision'] for name, value in intent.values.items()})
            _recovery_due.clear()
            _components.update({name: {'state': 'starting' if name in requested else 'stopped',
                'detail': '正在准备共享环境' if name in requested else '组件已停止',
                'version': None, 'pid': None, 'port': 5267 if name == 'kira' else 6099,
                'log': name + '.log', 'error': None, 'selected': name in selection, 'installed': installed[name],
                'installing': name == 'napcat' and install_napcat,
                'install_failed': name == 'napcat' and napcat_pending and not install_napcat} for name in _COMPONENTS})
            for name in blocked:
                _component_state(name, 'error', '自动恢复已暂停，请手动启动', error=intent.values[name]['blocked'])
            _rootfs_name = _resolved_paths = None
            _manager_ready = bool(_desired or _updates or _backups or _restores or _uninstalls)
        if not _manager_ready:
            _publish_components(home)
            return
        checkpoint(_cancel)
        _timezone = _android_timezone()
        _PHASE = 'verify_runtime'
        state(home, 'starting', '正在检查本机 Linux 运行环境', 0, stage=_PHASE)
        manifest = json.loads(assets_json) if isinstance(assets_json, str) else assets_json
        artifact, hashes = _abi_manifest(manifest, architecture)
        node_artifact, node_metadata = _node_manifest(manifest, architecture)
        _capabilities = _kernel_capabilities(node_metadata.get('minimum_kernel', '4.18'))
        _publish_capabilities(home)
        environment = _stage_native(native_dir, runtime, hashes)
        _PHASE = 'rootfs'
        root = _prepare_rootfs(runtime, artifact, home)
        # An uninstall interrupted after its commit point is finished before anything reads NapCat.
        napcat_runtime.recover_uninstall(root, home)
        # No guest process exists yet, so nothing the retention rules remove can be in use.
        _apply_retention(home, root)
        _rootfs_name = root.relative_to(home).as_posix()
        _resolved_paths = RuntimePaths(home, root).status_paths()
        napcat_pending = napcat_runtime.installation_pending(root)
        metadata = napcat_runtime.metadata(root) if napcat_runtime.has_installation_files(root) else {'installed': False}
        with _component_lock:
            _components['kira']['installed'] = _kira_installed(root)
            _components['napcat'].update(installed=metadata['installed'], version=metadata.get('version'),
                                         qq_version=metadata.get('qq_version'))
            if not metadata['installed'] and 'napcat' not in _installs:
                _desired.discard('napcat')
                _updates.discard('napcat')
                _component_state('napcat', 'stopped', '尚未安装本机 NapCat', install_failed=napcat_pending)
            if not (_desired or _updates or _backups or _restores or _uninstalls):
                _manager_ready = False
        if not _manager_ready:
            _publish_components(home)
            return
        node_prepared = False
        try:
            _prepare_node(runtime, root, node_artifact, node_metadata, home)
            node_prepared = True
        except InstallationCancelled:
            raise
        except Exception as error:
            checkpoint(_cancel)
            _node_unavailable(home, error)
        _PHASE = 'source_code'
        code = _stage_source(source_zip_path, root, home, manifest)
        _PHASE = 'guest_configuration'
        dns = json.loads(dns_servers_json) if isinstance(dns_servers_json, str) else dns_servers_json
        if not isinstance(dns, list): raise ValueError('DNS configuration must be a list')
        _configure_guest(root, dns, network_required=bool(_desired or _updates) and _bootstrap_requires_network(root, code))
        def watch():
            next_log_check = time.monotonic() + 300
            while not monitor_stop.wait(0.25):
                _observe_panels(home, boot_count)
                _monitor_components(home)
                if time.monotonic() >= next_log_check:
                    next_log_check = time.monotonic() + 300
                    # Running components keep their logs open; trim them in place once they pass the limit.
                    for log_name in ('kira.log', 'napcat.log', 'linux-bootstrap.log'):
                        try:
                            retention.truncate_log(runtime / log_name)
                        except OSError:
                            pass
        monitor = threading.Thread(target=watch, name='kira-component-monitor', daemon=True)
        monitor.start()
        cache_checked = False
        while not _cancel.is_set():
            with _component_lock:
                cache_ready = _component_processes.get('kira', {}).get('ready') is True
                now = time.monotonic()
                waiting = {name for name, (revision, due) in _recovery_due.items()
                           if _revisions[name] == revision and now < due}
                pending = [name for name in _COMPONENTS
                    if name not in _component_processes and name not in _component_busy and name not in waiting
                    and not _unsafe_stop and not _cleanup_blocked()
                    and (name in _desired or name in _updates or name in _backups or name in _restores
                         or name in _uninstalls)]
                if not pending and not _component_processes and not _component_busy and not waiting:
                    _manager_ready = False
                    break
            if cache_ready and not cache_checked:
                cache_checked = True
                try:
                    runtime_cache.complete(home, root, manifest, architecture, _operation_cancel,
                                           node_verified=_capabilities.get('node_available'))
                except InstallationCancelled:
                    raise
                except Exception as error:
                    # Cache retirement never turns a healthy component into a failure.
                    try:
                        _atomic_json(runtime / runtime_cache.REPORT,
                                     {'schema': 1, 'reclaimed_allocated_bytes': 0, 'retained': ['verification_failed'],
                                      'error': type(error).__name__})
                    except OSError:
                        pass
            if pending:
                _start_component(pending[0], native_dir, root, home, environment, code, node_prepared=node_prepared)
            else:
                _cancel.wait(0.25)
        for name in ('napcat', 'kira'):
            if name in _component_processes: _stop_component(name)
        _publish_components(home)
    except InstallationCancelled:
        for name in ('napcat', 'kira'):
            if name in _components: _stop_component(name)
        state(home, 'stopped', 'Linux 环境准备或核心启动已取消；可再次启动继续', stage='cancelled')
    except Exception as error:
        for name in _component_snapshot():
            if name in _component_processes: _stop_component(name)
            if _components[name]['state'] not in ('stopped', 'error'):
                _component_state(name, 'error', '共享环境准备失败', error=type(error).__name__)
        with (runtime / 'linux-bootstrap.log').open('ab') as log:
            log.write(('Linux bootstrap failed at ' + _PHASE + ' (' + type(error).__name__ + ')\n').encode('utf-8'))
        public_error = isinstance(error, (RuntimeCompatibilityError, RuntimeNetworkRequiredError, BaseSelectionError, SpaceUnavailable))
        state(home, 'error', str(error) if public_error else 'Linux 环境启动失败，请查看本机 linux-bootstrap.log 后重试',
              stage=_PHASE, error=str(error) if public_error else type(error).__name__ + ' at ' + _PHASE,
              network_required=isinstance(error, RuntimeNetworkRequiredError))
        raise
    finally:
        monitor_stop.set()
        if monitor is not None: monitor.join()
        # A control thread may still own a stop. Keep the run lock until that
        # ownership has been resolved; a new run must not reset its records.
        while _component_busy:
            time.sleep(0.05)
        with _component_lock:
            _manager_ready = False
            for name, (_, _, result) in list(_panel_jobs.items()):
                if result.done():
                    _panel_jobs.pop(name)
        # Publish the handshake closure together with the final component table.
        with _state_lock:
            current = _read_json(runtime / 'status.json', {})
            current.update(manager_ready=False, components=_component_snapshot())
            _atomic_json(runtime / 'status.json', current)


def recover_cleanup(home, native_dir, package, retry=False, owner_token=None):
    """Explicit idle-only proof and unlock, serialized with all runtime owners."""
    global _home, _intent_store, _service_owner, _process, _manager_ready
    if type(retry) is not bool or not re.fullmatch(r'[A-Za-z0-9_.]+', package):
        raise ValueError('Invalid cleanup recovery request')
    home = Path(home).resolve()
    runtime = RuntimePaths(home).state
    result = {'released': False, 'detail': '运行或停止任务尚未结束，请稍后重试'}
    if not _run_lock.acquire(blocking=False):
        return json.dumps(result, ensure_ascii=False)
    owns_stop = False
    admitted = False
    try:
        owns_stop = _stop_lock.acquire(blocking=False)
        if not owns_stop:
            return json.dumps(result, ensure_ascii=False)
        with _component_lock:
            if _manager_ready or _component_busy:
                return json.dumps(result, ensure_ascii=False)
            _home = home
            admitted = True
            _intent_store = RuntimeIntent(runtime / 'runtime-intent.json')
            _service_owner = owner_token
            _cancel.clear()
            _intent_store.consume_pending_stop()
            _intent_store.stop_all()
            _intent_store.commit({name: {'blocked': 'cleanup_unconfirmed'} for name in _COMPONENTS})
        verify_stopped(home, native_dir, package, _cancel, retry=retry)
        checkpoint(_cancel)
        # Match state()'s lock order so a late status publication cannot deadlock
        # against the final cleanup commit.
        with _state_lock, _component_lock:
            checkpoint(_cancel)
            tracked = [record['process'] for record in _component_processes.values()]
            if _process is not None:
                tracked.append(_process)
            if any(process.poll() is None for process in tracked):
                raise CleanupUnconfirmed('已有运行归属仍报告进程存活，已保留启动阻断')
            try:
                current = read_private_json(runtime / 'status.json')
            except FileNotFoundError:
                current = {}
            if not isinstance(current, dict) or not isinstance(current.get('components', {}), dict):
                raise CleanupUnconfirmed('运行状态记录无法解析，请保留日志后检查')
            components = current.setdefault('components', {})
            for name in _COMPONENTS:
                component = components.setdefault(name, {})
                if not isinstance(component, dict):
                    raise CleanupUnconfirmed('组件状态记录无法解析，请保留日志后检查')
                component.update(state='stopped', detail='已核验所属进程退出，可手动启动',
                                 running=False, installing=False, unsafe_stop=False, pid=0, error=None,
                                 wanted=False, intent_revision=_intent_store.values[name]['revision'] + 1,
                                 recovery_attempt=0, recovery_blocked=None)
                component.pop('health', None)
            current.update(state='stopped', detail='已核验所属进程退出，可手动启动',
                           manager_ready=False, components=components, progress=None)
            _atomic_json(runtime / 'status.json', current)
            _intent_store.commit({name: {'wanted': False, 'blocked': None, 'attempts': 0,
                                        'revision': value['revision'] + 1}
                                  for name, value in _intent_store.values.items()})
            for record in _component_processes.values():
                if record.get('log') is not None:
                    try:
                        record['log'].close()
                    except OSError:
                        pass
                record['process'].poll()
            if _process is not None:
                _process.poll()
            _process = None
            _component_processes.clear()
            _unsafe_stop.clear()
            _components.clear()
            _components.update(components)
            _desired.clear()
            _installs.clear()
            _updates.clear()
            _backups.clear()
            _restores.clear()
            _uninstalls.clear()
            _recovery_due.clear()
            _manager_ready = False
        result = {'released': True, 'detail': '已核验所属进程退出，可手动启动'}
    except (CleanupUnconfirmed, InstallationCancelled) as error:
        result['detail'] = str(error) or '核验已取消，启动阻断仍然保留'
    except Exception:
        result['detail'] = '核验或保存失败，启动阻断仍然保留，请查看设备存储与日志'
    finally:
        if owns_stop:
            if admitted:
                with _component_lock:
                    _intent_store = _service_owner = None
            _stop_lock.release()
        _run_lock.release()
    return json.dumps(result, ensure_ascii=False)


def stop(explicit=True, owner_token=None):
    """Call from a non-UI Android worker; preserve guest cleanup before tracer exit."""
    global _process
    with _component_lock:
        if owner_token != _service_owner:
            return False
        _cancel.set()
        _recovery_due.clear()
        if _active_cancel is not None: _active_cancel.set()
        if explicit and _intent_store is not None:
            try:
                if not _intent_store.consume_pending_stop():
                    _intent_store.stop_all()
            except OSError:
                for name in _components: _components[name]['intent_persistence_failed'] = True
        if not _stop_lock.acquire(blocking=False):
            return True
    try:
        for name in ('napcat', 'kira'):
            if name in _components: _stop_component(name)
        with _process_lock:
            process, home, run_id = _process, _home, _run_id
        if process is not None and home is not None:
            _cleanup_owned(process, home, run_id, None)
            _unsafe_stop.pop('environment', None)
    finally:
        _stop_lock.release()
    return True

# Base migration (plan R2B). Only an explicit user request reaches these entry points; startup
# never migrates on its own and still stops with base_change_requires_migration.
MIGRATION_CONFIRMATION = 'move-installed-kiraai-and-napcat'
ROLLBACK_CONFIRMATION = 'return-to-previous-base'


class _MigrationLease:
    """Hold the runtime's single execution slot while no component or stop is in flight."""

    def __init__(self, home):
        self.home = Path(home).resolve()
        self.cancel = threading.Event()
        self._gate = threading.Lock()
        self._committed = False
        self._entered = False

    def __enter__(self):
        global _active_cancel
        if not _run_lock.acquire(blocking=False):
            raise RuntimeError('A Linux backend or migration already owns the execution slot')
        try:
            with _component_lock, _process_lock:
                if (_manager_ready or _unsafe_stop or _component_processes or _component_busy
                        or _process is not None or _active_cancel is not None):
                    raise RuntimeError('Linux component ownership has not been released')
                try:
                    value = read_private_json(RuntimePaths(self.home).state / 'status.json')
                except FileNotFoundError:
                    value = {}
                if not isinstance(value, dict) or value.get('manager_ready'):
                    raise RuntimeError('Persisted Linux manager ownership is unresolved')
                components = value.get('components', {})
                if not isinstance(components, dict) or any(not isinstance(record, dict)
                        or record.get('unsafe_stop') or record.get('pid')
                        or record.get('state', 'stopped') not in ('stopped', 'error') for record in components.values()):
                    raise RuntimeError('Persisted Linux component ownership is unresolved')
                _cancel.clear()
                _active_cancel = self.cancel
                self._entered = True
        except BaseException:
            _run_lock.release()
            raise
        return self

    def __exit__(self, *exception):
        global _active_cancel
        with _component_lock:
            if _active_cancel is self.cancel:
                _active_cancel = None
        self._entered = False
        _run_lock.release()

    def request_cancel(self):
        """Cancel before activation; afterwards the binding switch has already happened."""
        with self._gate:
            if self._committed:
                return False
            self.cancel.set()
            return True

    def publish(self, replace_binding):
        with self._gate:
            if not self._entered or self._committed:
                raise RuntimeError('Migration activation has no exclusive owner')
            checkpoint(_operation_cancel)
            replace_binding()
            self._committed = True


def _migration_hooks(home, native_dir, hashes, dns, log):
    """Install packages and rebuild the venv inside a staged base, then prove its interpreter."""
    import napcat_runtime

    def execute(paths, argv, timeout=1800):
        environment = _stage_native(native_dir, paths.state, hashes)
        _command(native_dir, paths.root, paths.home, environment, argv, log, timeout=timeout)

    def prepare_target(paths, cancel, napcat_present):
        _configure_guest(paths.root, dns, network_required=True)
        environment = _stage_native(native_dir, paths.state, hashes)
        code = paths.from_guest(KIRA_PROGRAM)
        # System packages, a new venv and the official requirements, exactly as a first start does.
        _bootstrap(native_dir, paths.root, paths.home, environment, code, log, node_prepared=False)
        if napcat_present:
            attempt = paths.state / 'napcat' / 'attempts' / uuid.uuid4().hex
            attempt.mkdir(parents=True)
            def command(argv, stream, timeout=1800):
                return _command(native_dir, paths.root, paths.home, environment, argv, stream, timeout=timeout)
            napcat_runtime._dependencies(paths.root, paths.state / 'napcat', attempt, command, log, cancel,
                                         lambda *args, **kwargs: None)

    def verify_guest(paths, cancel):
        python = KIRA_VENV + '/bin/python'
        execute(paths, [python, '-m', 'pip', 'check'], timeout=300)
        probe = ('import hashlib, json, platform, sys; from pathlib import Path; '
                 'Path(sys.argv[1]).write_text(json.dumps({"prefix": sys.prefix, "version": platform.python_version(), '
                 '"requirements_sha256": hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()}))')
        execute(paths, [python, '-I', '-c', probe, HOST_STATE + '/venv-proof.json', KIRA_REQUIREMENTS], timeout=120)
        proof = json.loads((paths.state / 'venv-proof.json').read_text(encoding='utf-8'))
        if proof.get('prefix') != KIRA_VENV:
            raise ValueError('Target interpreter does not run from the KiraAI venv')
        return {'requirements_sha256': proof.get('requirements_sha256'), 'interpreter_verified': True,
                'dependency_check_passed': True}

    return prepare_target, verify_guest


def migrate_base(home, native_dir, assets_json, architecture, dns_servers_json, confirmation):
    """Move the stopped installation to this APK's base. Returns a JSON result for the caller to show."""
    import base_migration
    if confirmation != MIGRATION_CONFIRMATION:
        raise ValueError('Base migration requires the explicit user confirmation')
    home = Path(home).resolve()
    manifest = json.loads(assets_json) if isinstance(assets_json, str) else assets_json
    artifact, hashes = _abi_manifest(manifest, architecture)
    dns = json.loads(dns_servers_json) if isinstance(dns_servers_json, str) else dns_servers_json
    archive = RuntimePaths(home).state / 'downloads' / artifact.filename
    if not archive.is_file() or archive.is_symlink():
        return json.dumps({'migrated': False, 'detail': '新基础环境的安装包不在本机，请先打开应用让它导入后再迁移'},
                          ensure_ascii=False)
    lease = _MigrationLease(home)
    with (RuntimePaths(home).state / 'migration.log').open('ab', buffering=0) as log:
        prepare_target, verify_guest = _migration_hooks(home, native_dir, hashes, dns, log)
        try:
            target = base_migration.migrate(home, artifact, archive, _operation_cancel, exclusive_owner=lease,
                                            prepare_target=prepare_target, verify_guest=verify_guest,
                                            publish_binding=lease.publish)
        except (base_migration.MigrationRefused, SpaceUnavailable) as error:
            return json.dumps({'migrated': False, 'detail': str(error)}, ensure_ascii=False)
        except InstallationCancelled:
            return json.dumps({'migrated': False, 'detail': '已取消迁移，仍在使用原来的基础环境，可以再次迁移继续'},
                              ensure_ascii=False)
    return json.dumps({'migrated': True, 'root': target.relative_to(home).as_posix(),
                       'detail': '已迁移到新基础环境，原来的环境保留未删'}, ensure_ascii=False)


def rollback_base(home, confirmation):
    """Return to the base the last migration left, if the moved KiraAI program is unchanged."""
    import base_migration
    if confirmation != ROLLBACK_CONFIRMATION:
        raise ValueError('Base rollback requires the explicit user confirmation')
    home = Path(home).resolve()
    lease = _MigrationLease(home)
    try:
        root = base_migration.rollback(home, _operation_cancel, exclusive_owner=lease, publish_binding=lease.publish)
    except base_migration.MigrationRefused as error:
        return json.dumps({'rolled_back': False, 'detail': str(error)}, ensure_ascii=False)
    return json.dumps({'rolled_back': True, 'root': root.relative_to(home).as_posix(),
                       'detail': '已回到迁移前的基础环境，迁移后的 NapCat 与 QQ 数据已带回'}, ensure_ascii=False)
