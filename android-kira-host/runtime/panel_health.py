"""Bounded loopback HTTP and panel reachability observations for the host."""

import http.client
import io
import socket
import time


PROBE_INTERVAL_MS = 10_000
STALE_MS = 30_000
REQUEST_TIMEOUT = 1.0
_MAX_HTTP_OVERHEAD = 32 * 1024
_MAX_BODY = 32 * 1024
_BOOTTIME = getattr(time, 'CLOCK_BOOTTIME', None)


def _boottime():
    if _BOOTTIME is not None:
        try:
            return time.clock_gettime(_BOOTTIME)
        except OSError:
            pass
    return None


def boot_elapsed_ms():
    """Linux boot elapsed time includes sleep and matches Android elapsedRealtime."""
    value = _boottime()
    return int(value * 1000) if value is not None else None


def _io_time():
    # Transport timeouts still work on development platforms without BOOTTIME.
    # This fallback is never used for a serialized health timestamp.
    return time.clock_gettime(_BOOTTIME) if _IO_USES_BOOTTIME else time.monotonic()


_IO_USES_BOOTTIME = _boottime() is not None


def deadline_after(seconds=REQUEST_TIMEOUT):
    return _io_time() + seconds


def _remaining(deadline):
    remaining = deadline - _io_time()
    if remaining <= 0:
        raise TimeoutError('Local panel HTTP deadline reached')
    return remaining


class _DeadlineStream(io.RawIOBase):
    def __init__(self, stream, deadline, limit):
        self.stream = stream
        self.deadline = deadline
        self.remaining_bytes = limit

    def readable(self):
        return True

    def sendall(self, data):
        self.stream.settimeout(_remaining(self.deadline))
        self.stream.sendall(data)
        _remaining(self.deadline)

    def readinto(self, buffer):
        if self.remaining_bytes <= 0:
            raise ValueError('Local panel HTTP response exceeds the read budget')
        # Buffered readline/read can perform several recv calls under the hood.
        # Recompute the deadline for each one, even when bytes keep arriving.
        self.stream.settimeout(_remaining(self.deadline))
        count = self.stream.recv_into(buffer, min(len(buffer), self.remaining_bytes))
        _remaining(self.deadline)
        self.remaining_bytes -= count
        return count

    def makefile(self, mode):
        return io.BufferedReader(self)


def request(port, path, *, method='GET', headers=None, body=None, deadline=None,
            max_bytes=16_384, prefix=False):
    """Return the status and bounded body, using one deadline for every HTTP stage."""
    if not path.startswith('/') or path.startswith('//') or not 0 < max_bytes <= _MAX_BODY:
        raise ValueError('Invalid local panel HTTP request')
    if deadline is None:
        deadline = deadline_after()
    headers = dict(headers or {})
    # http.client accepts obsolete folded header values. Reject controls before
    # a local auth response can be forwarded as a Bearer header on a new socket.
    for name, value in headers.items():
        try:
            name_bytes = name.encode('ascii') if isinstance(name, str) else name
            value_bytes = value.encode('latin-1') if isinstance(value, str) else value
        except UnicodeError:
            raise ValueError('Invalid local panel HTTP header') from None
        if (not isinstance(name_bytes, bytes) or not isinstance(value_bytes, bytes)
                or any(byte < 32 or byte == 127 for part in (name_bytes, value_bytes) for byte in part)):
            raise ValueError('Invalid local panel HTTP header')
    headers['Connection'] = 'close'
    connection = http.client.HTTPConnection('127.0.0.1', port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.settimeout(_remaining(deadline))
        stream.connect(('127.0.0.1', port))
        bounded = _DeadlineStream(stream, deadline, _MAX_HTTP_OVERHEAD)
        connection.sock = bounded
        connection.request(method, path, body=body, headers=headers)
        with http.client.HTTPResponse(bounded, method=method) as response:
            response.begin()
            if response.status != 200:
                _remaining(deadline)
                return response.status, b''
            if not prefix and response.length is not None and response.length > max_bytes:
                raise ValueError('Local panel HTTP body exceeds the read budget')
            bounded.remaining_bytes += max_bytes + 1
            data = response.read(max_bytes if prefix else max_bytes + 1)
            _remaining(deadline)
            if len(data) > max_bytes:
                raise ValueError('Local panel HTTP body exceeds the read budget')
            if response.length and (not prefix or len(data) < max_bytes):
                raise http.client.IncompleteRead(data, response.length)
            return response.status, data


def probe(port, *, deadline=None):
    """Confirm the official panel serves HTML on its local port."""
    try:
        path = '/webui/' if port == 6099 else '/'
        status, body = request(port, path, headers={'Accept': 'text/html'},
                               max_bytes=8192 if port == 5267 else _MAX_BODY,
                               prefix=True, deadline=deadline)
        return status == 200 and b'<html' in body.lower()
    except (OSError, ValueError, http.client.HTTPException):
        return False


class HealthTracker:
    """Observation hysteresis for one component generation, with caller-owned locking."""

    def __init__(self):
        self._state = 'unknown'
        self._observed = None
        self._last_success = None
        self._consecutive_successes = 0
        self._consecutive_failures = 0

    def record(self, ok, observed_elapsed_ms):
        if observed_elapsed_ms is None:
            return
        if self._observed is not None:
            if observed_elapsed_ms <= self._observed:
                return
            if observed_elapsed_ms - self._observed >= STALE_MS:
                self._consecutive_successes = self._consecutive_failures = 0
        # A fresh sample starts a new confirmation streak for an unconfirmed panel.
        if self._state != 'unknown':
            self._state = self._effective_state(observed_elapsed_ms)
        self._observed = observed_elapsed_ms
        if ok:
            self._last_success = observed_elapsed_ms
            self._consecutive_failures = 0
            self._consecutive_successes = min(2, self._consecutive_successes + 1)
            if self._consecutive_successes == 2:
                self._state = 'reachable'
        else:
            self._consecutive_successes = 0
            self._consecutive_failures = min(3, self._consecutive_failures + 1)
            if self._consecutive_failures == 3:
                self._state = 'unreachable'

    def _effective_state(self, now_elapsed_ms):
        if self._observed is None or now_elapsed_ms is None or now_elapsed_ms < self._observed:
            return 'unknown'
        if now_elapsed_ms - self._observed >= STALE_MS:
            return 'expired'
        # Fresh confirmed failures remain visible after the last success ages out.
        if self._state == 'reachable' and now_elapsed_ms - self._last_success >= STALE_MS:
            return 'expired'
        return self._state

    def snapshot(self, now_elapsed_ms):
        return {'state': self._effective_state(now_elapsed_ms),
                'observed_elapsed_ms': self._observed,
                'last_success_elapsed_ms': self._last_success}
