"""Durable per-component intent and bounded recovery decisions."""

import json
import os
from pathlib import Path
import stat
import uuid


COMPONENTS = ('kira', 'napcat')
RECOVERY_DELAYS = (1, 4)
STABLE_SECONDS = 300
_EMPTY = {'wanted': None, 'revision': 0, 'attempts': 0, 'blocked': None}
_BLOCKERS = (None, 'startup_failed', 'outer_exit', 'retries_exhausted', 'cleanup_unconfirmed')


class RuntimeIntent:
    def __init__(self, path):
        self.path = Path(path)
        self.values = self._read()

    def _read(self):
        if self.path.is_symlink():
            raise ValueError('Unsafe runtime intent path')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return {name: dict(_EMPTY) for name in COMPONENTS}
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Runtime intent is not a regular file')
            raw = stream.read(2049)
        if len(raw) > 2048:
            raise ValueError('Runtime intent exceeds its size limit')
        payload = json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict) or type(payload.get('schema')) is not int or payload['schema'] != 1:
            raise ValueError('Invalid runtime intent schema')
        values = payload.get('components')
        if not isinstance(values, dict) or set(values) != set(COMPONENTS):
            raise ValueError('Invalid runtime intent components')
        for value in values.values():
            if not isinstance(value, dict) or set(value) != set(_EMPTY):
                raise ValueError('Invalid component runtime intent')
            if (value['wanted'] is not None and type(value['wanted']) is not bool
                    or type(value['revision']) is not int or value['revision'] < 0
                    or type(value['attempts']) is not int or not 0 <= value['attempts'] <= len(RECOVERY_DELAYS)
                    or value['blocked'] not in _BLOCKERS):
                raise ValueError('Invalid component runtime intent values')
        return values

    def commit(self, changes):
        """The caller serializes decisions and retains the preimage on I/O failure."""
        values = {name: {**value, **changes.get(name, {})} for name, value in self.values.items()}
        if values == self.values:
            return
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'components': values}, stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self.values = values

    def stop_all(self):
        changes = {}
        for name, value in self.values.items():
            blocked = 'cleanup_unconfirmed' if value['blocked'] == 'cleanup_unconfirmed' else None
            if value['wanted'] is not False or value['attempts'] or value['blocked'] != blocked:
                changes[name] = {'wanted': False, 'revision': value['revision'] + 1,
                                 'attempts': 0, 'blocked': blocked}
        self.commit(changes)

    def consume_pending_stop(self):
        """Claim the pre-bridge Java stop without deleting a newer journal."""
        pending = self.path.with_name('manual-stop-pending')
        applying = self.path.with_name('manual-stop-applying')
        try:
            os.replace(pending, applying)
        except FileNotFoundError:
            if not applying.exists() and not applying.is_symlink():
                return False
        self.stop_all()
        applying.unlink()
        return True
