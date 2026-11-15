"""Write-once private history of the first default panel credentials.

Existing installations without history remain explicitly unavailable. Current
configuration values must never be used to reconstruct an unknown original.
"""
import json
import os
import time
from runtime_paths import RuntimePaths


def remember_initial_token(home, component, token=None):
    if component not in ('kira', 'napcat'):
        raise ValueError('Unknown panel component')
    valid = isinstance(token, str) and bool(token.strip()) and len(token) <= 4096
    value = {'version': 1, 'state': 'recorded' if valid else 'unavailable',
             'created_at': int(time.time())}
    if valid:
        value['token'] = token
    try:
        folder = RuntimePaths(home).state
        if folder.is_symlink():
            return False
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / ('initial-' + component + '-token.json')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        return True
    except (OSError, ValueError):
        # Missing/corrupt history is reported as unavailable; do not replace it
        # with a possibly edited current token or break the running component.
        return False
