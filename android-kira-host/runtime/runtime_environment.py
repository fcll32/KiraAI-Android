"""Read user guest variables without exposing values to host command logs."""
import json
import re
from runtime_paths import RuntimePaths

MAX_BYTES = 65536
MAX_VARIABLES = 128
RESERVED = frozenset({'KIRA_LINUX_RUN_ID', 'KIRA_LINUX_HOST_PID', 'KIRA_NODE_AVAILABLE'})
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')


class EnvironmentConfigurationError(ValueError):
    """A bounded, value-free diagnostic safe for the host log."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnvironmentConfigurationError('Environment file contains duplicate variable names')
        result[key] = value
    return result


def load_environment(home):
    """Reload the atomic private settings file for each new guest process.

    These settings apply inside PRoot after env -i. They never modify Android's
    loader environment or the running components' existing environment.
    """
    defaults = {'KIRA_TELEMETRY_ENABLED': '0'}
    try:
        path = RuntimePaths(home).state / 'environment.json'
        if path.is_symlink():
            raise EnvironmentConfigurationError('Environment file must be a regular private file')
        if not path.exists():
            return defaults
        if not path.is_file():
            raise EnvironmentConfigurationError('Environment file must be a regular private file')
        with path.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
    except EnvironmentConfigurationError:
        raise
    except (OSError, ValueError):
        raise EnvironmentConfigurationError('Environment file could not be read') from None
    if len(data) > MAX_BYTES:
        raise EnvironmentConfigurationError('Environment file exceeds 65536 UTF-8 bytes')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object)
    except EnvironmentConfigurationError:
        raise
    except (UnicodeError, ValueError):
        raise EnvironmentConfigurationError('Environment file must contain valid UTF-8 JSON') from None
    if not isinstance(value, dict):
        raise EnvironmentConfigurationError('Environment file must be a JSON object')
    if len(value) > MAX_VARIABLES:
        raise EnvironmentConfigurationError('Environment file exceeds 128 variables')
    for key, item in value.items():
        if not _NAME.fullmatch(key):
            raise EnvironmentConfigurationError('Environment file contains an invalid variable name')
        if key in RESERVED:
            raise EnvironmentConfigurationError('Environment file contains a reserved host variable')
        if not isinstance(item, str) or any(character in item for character in '\0\r\n'):
            raise EnvironmentConfigurationError('Environment values must be single-line strings without NUL')
    defaults.update(value)
    return defaults
