"""Read-only, redacted inventory for a blocked private guest base change."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

from linux_installer import checkpoint
from runtime_paths import (KIRA_PROGRAM, KIRA_REQUIREMENTS, KIRA_VENV, NAPCAT_DATA,
                           NAPCAT_PROGRAM, RuntimePaths, existing_generations, generation_digest,
                           read_private_file, read_private_json)


def _public_match(path, pattern):
    try:
        text = read_private_file(path, 128 * 1024).decode('utf-8')
        match = re.search(pattern, text, re.MULTILINE)
        return match.group(1) if match else None
    except (OSError, ValueError):
        return None


def _requirements_identity(path):
    try:
        record = read_private_json(path)
    except (OSError, ValueError):
        return None
    value = record.get('sha256') if isinstance(record, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else None


def _size(path, budget, cancel, *, exclude_root_child=None):
    """Count regular-file logical bytes without following links or crossing mounts."""
    try:
        first = path.lstat()
    except FileNotFoundError:
        return {'present': False, 'logical_bytes': 0, 'complete': True}
    except OSError:
        return {'present': None, 'logical_bytes': 0, 'complete': False}
    total = 0
    complete = True
    directories = [path]
    while directories:
        checkpoint(cancel)
        entry = directories.pop()
        try:
            info = entry.lstat()
            if info.st_dev != first.st_dev:
                complete = False
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
            elif stat.S_ISDIR(info.st_mode):
                with os.scandir(entry) as children:
                    for child in children:
                        checkpoint(cancel)
                        if entry == path and child.name == exclude_root_child:
                            continue
                        if budget[0] == 0:
                            return {'present': True, 'logical_bytes': total, 'complete': False}
                        budget[0] -= 1
                        directories.append(Path(child.path))
        except OSError:
            complete = False
    return {'present': True, 'logical_bytes': total, 'complete': complete}


def report(home, artifact, reason, cancel):
    import napcat_runtime

    paths = RuntimePaths(home)
    home = paths.home
    budget = [40000]
    roots = []
    data = _size(paths.data, budget, cancel)
    truncated = False
    for index, root in enumerate(existing_generations(home)):
        if index >= 8:
            truncated = True
            break
        checkpoint(cancel)
        identity = generation_digest(root)
        entry = {'root': root.relative_to(home).as_posix(), 'sha256': identity}
        if identity is not None:
            current = RuntimePaths(home, root)
            try:
                program = current.from_guest(KIRA_PROGRAM)
                venv = current.from_guest(KIRA_VENV)
                requirements = current.from_guest(KIRA_REQUIREMENTS)
                code_size = _size(program, budget, cancel, exclude_root_child='venv')
                venv_size = _size(venv, budget, cancel)
                napcat = napcat_runtime.metadata(root)
                entry.update(paths=current.status_paths(), program=code_size, python_environment=venv_size,
                             napcat_program=_size(current.from_guest(NAPCAT_PROGRAM), budget, cancel),
                             napcat_data=_size(current.from_guest(NAPCAT_DATA), budget, cancel),
                             kira_version=_public_match(program / 'core/config/default.py', r'^VERSION\s*=\s*["\'](v?\d{1,5}\.\d{1,5}\.\d{1,5})["\']'),
                             napcat_version=napcat['version'] if re.fullmatch(r'v\d{1,5}\.\d{1,5}\.\d{1,5}', napcat['version']) else None,
                             qq_version=napcat['qq_version'] if re.fullmatch(r'\d{1,5}\.\d{1,5}\.\d{1,5}-\d{1,9}', napcat['qq_version']) else None,
                             requirements_present=requirements.is_file(),
                             requirements_sha256=_file_identity(requirements),
                             requirements_install_sha256=_requirements_identity(venv / '.kira-requirements.json'),
                             python_version=_public_match(venv / 'pyvenv.cfg', r'^version\s*=\s*(\d{1,4}\.\d{1,4}\.\d{1,4})\s*$'))
            except (OSError, ValueError, RuntimeError):
                entry['inventory_unavailable'] = True
        roots.append(entry)
    required = max((item.get('program', {}).get('logical_bytes', 0)
                    + item.get('napcat_program', {}).get('logical_bytes', 0)
                    + item.get('napcat_data', {}).get('logical_bytes', 0) for item in roots), default=0)
    try:
        free = shutil.disk_usage(paths.state if paths.state.is_dir() else home).free
    except OSError:
        free = None
    return {'version': 1, 'decision': 'blocked', 'reason': reason,
            'requested_base_sha256': artifact.sha256, 'existing_bases': roots, 'base_inventory_truncated': truncated,
            'kira_data': {'path': paths.data.relative_to(home).as_posix(), **data},
            'space': {'available_bytes': free,
                      'target_archive_bytes': artifact.size,
                      'archive_and_copy_lower_bound_bytes': artifact.size + required + data['logical_bytes'],
                      'estimate_excludes_extraction_rebuild_and_safety_margin': True},
            'python_environment': {'rebuild_required_for_base_change': True,
                                   'identity_inputs': ['python_abi', 'architecture', 'glibc', 'requirements_sha256',
                                                       'system_libraries', 'interpreter_path']}}


def _file_identity(path):
    try:
        return hashlib.sha256(read_private_file(path, 1024 * 1024)).hexdigest()
    except (OSError, ValueError):
        return None
