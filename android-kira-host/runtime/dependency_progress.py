"""Parse supported pip/APT CLI progress without treating log text as state."""
import math
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

MAX_LINE = 8192
MAX_READ = 256 * 1024
MAX_COUNTER = (1 << 63) - 1


def safe_filename(value):
    """Keep only a short archive basename; never expose URL auth or queries."""
    try:
        name = unquote(urlsplit(value).path).rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    except ValueError:
        return 'Python 依赖'
    if (re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+!-]{0,159}', name)
            and name.lower().endswith(('.whl', '.metadata', '.zip', '.tar.gz', '.tar.bz2', '.tar.xz'))):
        return name
    return 'Python 依赖'


class DependencyProgress:
    """Consume complete CLI records; unknown totals stay indeterminate."""
    def __init__(self, tool, clock=time.monotonic):
        if tool not in {'pip', 'apt', 'install'}:
            raise ValueError('Unsupported dependency progress source')
        self.tool, self.clock = tool, clock
        self.partial = b''
        self.discarding = False
        self.started = clock()
        self.first_bytes = 0
        self.seen_raw = False
        self.value = {'transfer_active': True, 'transfer_kind': 'install' if tool == 'install' else 'resolve',
            'transfer_label': '正在安装本地依赖' if tool == 'install' else '正在解析系统依赖' if tool == 'apt' else '正在解析 Python 依赖',
            'transfer_done_bytes': 0, 'transfer_total_bytes': 0, 'transfer_percent': None,
            'transfer_bytes_per_second': 0.0, 'transfer_complete': False}

    def snapshot(self):
        return dict(self.value)

    def _new_file(self, label, kind='download'):
        self.started, self.first_bytes = self.clock(), 0
        self.seen_raw = False
        self.value.update(transfer_kind=kind, transfer_label=label,
            transfer_done_bytes=0, transfer_total_bytes=0, transfer_percent=None,
            transfer_bytes_per_second=0.0, transfer_complete=False)

    def _line(self, raw):
        line = raw.decode('utf-8', errors='replace').strip()
        if self.tool == 'pip':
            name = re.match(r'(Downloading|Using cached)\s+(\S+)', line)
            if name:
                self._new_file(safe_filename(name[2]), 'download' if name[1] == 'Downloading' else 'resolve')
                return self.snapshot()
            progress = re.fullmatch(r'Progress ([0-9]{1,19}) of ([0-9]{1,19})', line)
            if progress:
                done, total = map(int, progress.groups())
                if done > MAX_COUNTER or total > MAX_COUNTER:
                    return None
                previous = self.value['transfer_done_bytes']
                if self.value['transfer_kind'] != 'download' or done < previous:
                    self._new_file(self.value['transfer_label'] if self.value['transfer_kind'] == 'download' else 'Python 依赖下载')
                if not self.seen_raw:
                    self.first_bytes = done
                    self.seen_raw = True
                elapsed = max(0.001, self.clock() - self.started)
                percent = round(done * 100 / total, 2) if total and done <= total else None
                self.value.update(transfer_kind='download', transfer_done_bytes=done,
                    transfer_total_bytes=total, transfer_percent=percent,
                    transfer_bytes_per_second=round(max(0, done - self.first_bytes) / elapsed, 2))
                return self.snapshot()
            if line.startswith(('Installing collected packages:', 'Successfully installed ', 'Building wheel for ',
                                'Preparing metadata ', 'Installing build dependencies ')):
                self._new_file('正在安装 Python 依赖', 'install')
                return self.snapshot()
        elif self.tool == 'apt':
            fields = line.split(':')
            if len(fields) >= 4 and fields[0] in {'dlstatus', 'pmstatus'}:
                # APT package names can have an architecture suffix containing ':'.
                position = 2
                while position < min(len(fields) - 1, 5):
                    try:
                        percent = float(fields[position])
                        break
                    except ValueError:
                        position += 1
                else:
                    return None
                if not math.isfinite(percent) or not 0 <= percent <= 100:
                    return None
                downloading = fields[0] == 'dlstatus'
                package = ':'.join(fields[1:position])
                if downloading:
                    label = '正在下载系统依赖'
                    if re.fullmatch(r'[0-9]{1,8}', package):
                        label += '（已完成 ' + package + ' 个文件）'
                else:
                    label = '正在安装系统依赖'
                    if re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?', package) and len(package) <= 128:
                        label += '：' + package
                self.value.update(transfer_kind='download' if downloading else 'install', transfer_label=label,
                    transfer_done_bytes=0, transfer_total_bytes=0, transfer_percent=round(percent, 2),
                    transfer_bytes_per_second=0.0)
                return self.snapshot()
        return None

    def feed(self, data):
        if not isinstance(data, bytes):
            raise TypeError('Progress log chunks must be bytes')
        self.partial += data.replace(b'\r', b'\n')
        events = []
        while b'\n' in self.partial:
            line, self.partial = self.partial.split(b'\n', 1)
            if not self.discarding and len(line) <= MAX_LINE:
                value = self._line(line)
                if value is not None:
                    events.append(value)
            self.discarding = False
        if len(self.partial) > MAX_LINE:
            self.partial = b''
            self.discarding = True
        return events

    def finish(self, success):
        # A successful install is not evidence of the final byte count for a
        # transfer with an unknown Content-Length; preserve the last real values.
        self.value.update(transfer_active=False, transfer_complete=bool(success))
        return self.snapshot()


class ProgressLogTail:
    """Read only new bounded bytes from an append-only command log."""
    def __init__(self, path, offset, parser):
        self.path, self.offset, self.parser = Path(path), max(0, int(offset)), parser

    def poll(self):
        with self.path.open('rb') as stream:
            size = stream.seek(0, 2)
            if size < self.offset:
                self.offset = 0
                self.parser.partial = b''
                self.parser.discarding = False
            stream.seek(self.offset)
            data = stream.read(MAX_READ)
            self.offset += len(data)
        return self.parser.feed(data)
