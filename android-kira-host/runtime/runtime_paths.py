"""Physical paths for the private guest and their corresponding Linux paths."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


RUNTIME_DIR = 'linux-runtime'
LEGACY_RUNTIME_DIR = 'kira-linux'
KIRA_DATA_DIR = 'kira-core'
ROOT_MARKER = '.kira-rootfs.json'
BASE_BINDING = 'base-binding.json'
PREFLIGHT_REPORT = 'migration-preflight.json'

KIRA_PROGRAM = '/opt/KiraAI'
KIRA_REQUIREMENTS = KIRA_PROGRAM + '/requirements.txt'
KIRA_VENV = KIRA_PROGRAM + '/venv'
KIRA_DATA = '/var/lib/kira'
HOST_STATE = '/run/kira-host'
GUEST_SHM = '/dev/shm'
NAPCAT_PROGRAM = '/root/Napcat'
NAPCAT_QQ = NAPCAT_PROGRAM + '/opt/QQ'
NAPCAT_CODE = NAPCAT_QQ + '/resources/app/app_launcher/napcat'
NAPCAT_DATA = '/root/.config/QQ'
NAPCAT_CONFIG = NAPCAT_CODE + '/config/webui.json'
NAPCAT_PENDING = '/.napcat-host-pending.json'

_DIGEST = re.compile(r'[0-9a-f]{64}')
_GENERATION = re.compile(r'rootfs-[0-9a-f]{16}')
_STATE_LOCATIONS = (KIRA_PROGRAM, KIRA_DATA, NAPCAT_PROGRAM, NAPCAT_DATA, NAPCAT_PENDING)


class BaseSelectionError(ValueError):
    """The current environment cannot be selected without explicit migration."""

    def __init__(self, reason):
        super().__init__('已保留原 Linux 环境，基础环境变更须先完成迁移预检')
        self.reason = reason


def private_path(directory, relative):
    directory = Path(directory)
    parts = relative.split('/')
    if not parts or any(part in ('', '.', '..') or '\\' in part or ':' in part or '\0' in part for part in parts):
        raise ValueError('Invalid private relative path')
    target = directory.joinpath(*parts)
    if not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError('Path escapes its private directory')
    return target


def read_private_file(path, limit):
    if path.is_symlink():
        raise ValueError('Private file is a symbolic link')
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('Private file is not regular')
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Private file exceeds its size limit')
    return content


def read_private_json(path, limit=65536):
    return json.loads(read_private_file(path, limit).decode('utf-8'))


def root_path(root, guest):
    if not guest.startswith('/'):
        raise ValueError('Expected an absolute guest path')
    return private_path(root, guest[1:])


class RuntimePaths:
    def __init__(self, home, root=None):
        self.home = Path(home)
        self.root = Path(root) if root is not None else None
        self.state = private_path(self.home, RUNTIME_DIR)
        self.data = private_path(self.home, KIRA_DATA_DIR)
        if self.state.is_symlink() or self.data.is_symlink():
            raise ValueError('Private runtime or data directory is a symbolic link')

    def from_guest(self, guest):
        for prefix, directory in self.bindings():
            if guest == prefix:
                return directory
            if guest.startswith(prefix + '/'):
                return private_path(directory, guest[len(prefix) + 1:])
        return root_path(self.root, guest)

    def bindings(self):
        return ((GUEST_SHM, private_path(self.state, 'shm')), (KIRA_DATA, self.data), (HOST_STATE, self.state))

    def as_guest_state(self, path):
        relative = Path(path).resolve().relative_to(self.state.resolve())
        return str(PurePosixPath(HOST_STATE, *relative.parts))

    def status_paths(self):
        paths = {'kira_program': KIRA_PROGRAM, 'napcat_program': NAPCAT_PROGRAM,
                 'napcat_data': NAPCAT_DATA, 'napcat_config': NAPCAT_CONFIG}
        # These identities are lexical. Each actual reader checks confinement.
        return {'kira_data': self.data.relative_to(self.home).as_posix(),
                **{key: (self.root / guest[1:]).relative_to(self.home).as_posix() for key, guest in paths.items()}}


def existing_generations(home):
    for location in (RUNTIME_DIR, LEGACY_RUNTIME_DIR):
        directory = private_path(home, location)
        if directory.is_symlink() or (os.path.lexists(directory) and not directory.is_dir()):
            raise BaseSelectionError('invalid_runtime_directory')
        yield from sorted(path for path in directory.glob('rootfs-*') if _GENERATION.fullmatch(path.name))


def generation_digest(root):
    if root.is_symlink() or not root.is_dir() or not _GENERATION.fullmatch(root.name):
        return None
    try:
        marker = read_private_json(root / ROOT_MARKER)
    except (OSError, ValueError):
        return None
    digest = marker.get('sha256') if isinstance(marker, dict) else None
    return digest if isinstance(digest, str) and _DIGEST.fullmatch(digest) and root.name == 'rootfs-' + digest[:16] else None


def _contains_component(root):
    return any(os.path.lexists(root / guest[1:]) for guest in _STATE_LOCATIONS)


def bound_base(home):
    home = Path(home)
    state = RuntimePaths(home).state
    binding_path = state / BASE_BINDING
    try:
        binding = read_private_json(binding_path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise BaseSelectionError('invalid_base_binding') from None
    identity = binding.get('root') if isinstance(binding, dict) else None
    if (not isinstance(binding, dict) or binding.get('version') != 1 or not isinstance(identity, str)
            or not re.fullmatch(r'(linux-runtime|kira-linux)/rootfs-[0-9a-f]{16}', identity)):
        raise BaseSelectionError('invalid_base_binding')
    try:
        root = private_path(home, identity)
    except ValueError:
        raise BaseSelectionError('invalid_base_binding') from None
    recorded = binding.get('sha256')
    if root.parent.is_symlink() or not isinstance(recorded, str) or generation_digest(root) != recorded:
        raise BaseSelectionError('unverified_bound_base')
    return root, recorded


def readable_generations(home):
    try:
        bound = bound_base(home)
        return (bound[0],) if bound else tuple(root for root in existing_generations(home) if generation_digest(root))
    except (OSError, ValueError, RuntimeError):
        # Startup preflight reports the problem; readers cannot trust a bad binding.
        return ()


def choose_base(home, digest):
    home = Path(home)
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError('Invalid requested base digest')
    state = RuntimePaths(home).state
    bound = bound_base(home)
    if bound:
        root, recorded = bound
        if recorded != digest:
            raise BaseSelectionError('base_change_requires_migration')
        return root

    generations = [(root, generation_digest(root)) for root in existing_generations(home)]
    if any(identity is None for _, identity in generations):
        raise BaseSelectionError('unverified_existing_base')
    candidates = [root for root, identity in generations if identity == digest]
    populated = [root for root, _ in generations if _contains_component(root)]
    if len(populated) > 1 or any(root not in candidates for root in populated):
        raise BaseSelectionError('base_change_requires_migration')
    if populated:
        return populated[0]
    if candidates:
        return candidates[0]
    record = state / 'official-install.json'
    if os.path.lexists(record) or os.path.lexists(RuntimePaths(home).data):
        raise BaseSelectionError('base_change_requires_migration')
    return state / ('rootfs-' + digest[:16])


def binding_value(home, root, digest):
    return {'version': 1, 'root': root.relative_to(home).as_posix(), 'sha256': digest}
