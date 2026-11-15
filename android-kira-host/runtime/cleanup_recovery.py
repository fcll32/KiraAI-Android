"""Conservative same-UID process proof for an explicitly requested unlock."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal

from linux_installer import checkpoint


class CleanupUnconfirmed(RuntimeError):
    pass


@dataclass(frozen=True)
class OwnedProcess:
    pid: int
    birth: str
    tracer: bool


def _read(path, limit):
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise CleanupUnconfirmed('进程信息超过核验范围，请保留日志后重试')
    return value


def inspect_processes(home, native_dir, package, cancel, *, proc=Path('/proc')):
    """Unknown same-UID processes prevent proof, including unreadable metadata."""
    if not proc.is_dir() or not hasattr(os, 'getuid'):
        raise CleanupUnconfirmed('当前系统无法核验进程归属')
    owned = []
    uid = os.getuid()
    home, native_dir = Path(home).resolve(), Path(native_dir).resolve()
    for folder in proc.iterdir():
        checkpoint(cancel)
        if not folder.name.isdigit() or int(folder.name) == os.getpid():
            continue
        try:
            if folder.stat().st_uid != uid:
                continue
            fields = _read(folder / 'stat', 8192).rpartition(b')')[2].split()
            if len(fields) < 20:
                raise CleanupUnconfirmed('进程身份信息不完整')
            if fields[0] == b'Z':
                continue
            birth = fields[19].decode('ascii')
            argv = _read(folder / 'cmdline', 131072).split(b'\0')
            environment = _read(folder / 'environ', 262144).split(b'\0')
            executable = Path(os.readlink(folder / 'exe').removesuffix(' (deleted)')).resolve()
            marked = any(re.fullmatch(rb'KIRA_LINUX_RUN_ID=[0-9a-f]{32}', value)
                         for value in argv + environment)
            tracer = executable == native_dir / 'libproot.so'
            guest_file = any(executable.is_relative_to(home / name) for name in ('linux-runtime', 'kira-linux'))
            if marked or tracer or guest_file or executable == native_dir / 'libprootloader.so':
                owned.append(OwnedProcess(int(folder.name), birth, tracer))
            elif not (executable.name in ('app_process32', 'app_process64', 'app_process')
                      and argv[0] in (package.encode(), (package + ':host_runtime').encode())):
                raise CleanupUnconfirmed('仍有无法确认用途的本应用进程，暂时不能解除')
        except FileNotFoundError:
            try:
                remaining = _read(folder / 'stat', 8192).rpartition(b')')[2].split()
                if remaining and remaining[0] == b'Z':
                    continue
            except FileNotFoundError:
                continue
            raise CleanupUnconfirmed('进程仍存在但归属信息不完整，暂时不能解除')
        except (OSError, UnicodeError, ValueError) as error:
            raise CleanupUnconfirmed('无法读取完整进程信息，暂时不能解除') from error
    return owned


def _signal(process, value, proc):
    try:
        fields = _read(proc / str(process.pid) / 'stat', 8192).rpartition(b')')[2].split()
        if len(fields) < 20:
            raise CleanupUnconfirmed('清理前无法复核进程身份')
        if fields[19].decode('ascii') == process.birth and fields[0] != b'Z':
            os.kill(process.pid, value)
    except FileNotFoundError:
        pass
    except ProcessLookupError:
        pass


def verify_stopped(home, native_dir, package, cancel, *, retry=False, proc=Path('/proc')):
    processes = inspect_processes(home, native_dir, package, cancel, proc=proc)
    if processes and not retry:
        raise CleanupUnconfirmed('仍有所属进程存活，请先重试清理')
    if retry:
        # Keep tracers available while guest children receive their signals.
        for tracer in (False, True):
            for value in (signal.SIGTERM, signal.SIGKILL):
                for process in processes:
                    checkpoint(cancel)
                    if process.tracer == tracer:
                        _signal(process, value, proc)
                if processes:
                    cancel.wait(.3)
                processes = inspect_processes(home, native_dir, package, cancel, proc=proc)
    for _ in range(2):
        checkpoint(cancel)
        if inspect_processes(home, native_dir, package, cancel, proc=proc):
            raise CleanupUnconfirmed('仍有所属进程存活，已保留启动阻断')
        cancel.wait(.1)
    checkpoint(cancel)
