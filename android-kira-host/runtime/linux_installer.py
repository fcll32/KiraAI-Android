"""Verified, cancellable downloads and confined Linux rootfs staging."""
from dataclasses import dataclass
import hashlib
import errno
import io
import os
from pathlib import Path, PurePosixPath
import re
import select
import ssl
import tarfile
import shutil
import time
from http.client import HTTPResponse, HTTPSConnection, IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from domestic_sources import download_urls

CHUNK = 128 * 1024
MAX_ROOTFS_BYTES = 4 * 1024 * 1024 * 1024
MAX_ROOTFS_FILES = 500_000
_DOWNLOAD_IDLE_SECONDS = 15
_DOWNLOAD_REQUEST_SECONDS = 15 * 60
_DOWNLOAD_CANCEL_POLL_SECONDS = 0.1


class InstallationCancelled(Exception):
    pass


def checkpoint(cancel):
    if cancel.is_set():
        raise InstallationCancelled('Linux environment preparation cancelled')


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('Runtime download request deadline reached')
    return seconds


class _DownloadStream(io.RawIOBase):
    """Bound response reads, including proxy and HTTP headers and chunk framing."""

    def __init__(self, sock, cancel, deadline):
        self.sock = sock
        self.cancel = cancel
        self.deadline = deadline
        # A plaintext CONNECT response precedes TLS on the same socket.
        self.tunnel_timeout = None if isinstance(sock, ssl.SSLSocket) else sock.gettimeout()
        # urllib closes its socket handle after headers. Keep the underlying
        # descriptor alive just as a regular socket.makefile() response does.
        self.reference = sock.makefile('rb', buffering=0)
        self.sock.setblocking(False)

    def readable(self):
        return True

    def makefile(self, _mode):
        return io.BufferedReader(self)

    def readinto(self, buffer):
        idle_deadline = time.monotonic() + _DOWNLOAD_IDLE_SECONDS
        while True:
            checkpoint(self.cancel)
            seconds = min(_remaining(self.deadline), _remaining(idle_deadline), _DOWNLOAD_CANCEL_POLL_SECONDS)
            try:
                return self.sock.recv_into(buffer)
            except (BlockingIOError, ssl.SSLWantReadError):
                select.select([self.sock], [], [], seconds)
            except ssl.SSLWantWriteError:
                select.select([], [self.sock], [], seconds)

    def close(self):
        if not self.closed:
            try:
                if self.tunnel_timeout is not None and self.sock.fileno() >= 0:
                    timeout = min(self.tunnel_timeout, self.deadline - time.monotonic())
                    self.sock.settimeout(max(0.001, timeout))
            finally:
                self.reference.close()
        super().close()


class _DownloadHTTPSHandler(HTTPSHandler):
    def __init__(self, context, cancel, deadline):
        super().__init__(context=context)
        self.cancel = cancel
        self.deadline = deadline

    def https_open(self, request):
        def connect(host, timeout, **options):
            checkpoint(self.cancel)
            connection = HTTPSConnection(host, timeout=min(timeout, _remaining(self.deadline)), **options)
            connection.response_class = self.response
            return connection

        return self.do_open(connect, request, context=self._context)

    def response(self, sock, *args, **options):
        return HTTPResponse(_DownloadStream(sock, self.cancel, self.deadline), *args, **options)


class _DownloadRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlsplit(new_url).scheme != 'https':
            raise ValueError('Runtime download redirected away from HTTPS')
        return super().redirect_request(request, response, code, message, headers, new_url)


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    sha256: str
    size: int

    def __post_init__(self):
        address = urlsplit(self.url)
        if address.scheme != 'https' or not address.hostname or address.username or address.password:
            raise ValueError('Runtime downloads require an HTTPS URL without credentials')
        if self.filename in ('', '.', '..') or Path(self.filename).name != self.filename or '/' in self.filename or '\\' in self.filename:
            raise ValueError('Invalid runtime artifact filename')
        if not re.fullmatch('[0-9a-f]{64}', self.sha256) or type(self.size) is not int or not 0 < self.size <= MAX_ROOTFS_BYTES:
            raise ValueError('Runtime artifact requires a pinned SHA256 and bounded exact size')


def digest(path, cancel):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(CHUNK):
            checkpoint(cancel)
            value.update(chunk)
    return value.hexdigest()


def download(artifact, cache, cancel, progress=lambda *_: None, opener=None, *, range_bytes=None):
    """Resume a bounded number of transient network failures from verified input."""
    if range_bytes is not None and (type(range_bytes) is not int or not CHUNK <= range_bytes <= 4 * 1024 * 1024):
        raise ValueError('Invalid bounded download range')
    sources = download_urls(artifact.url)
    for source_index, source in enumerate(sources):
        try:
            while True:
                for attempt in range(3):
                    checkpoint(cancel)
                    try:
                        result = _download_once(artifact, cache, cancel, progress, opener, range_bytes, source)
                        if result is not None:
                            return result
                        break
                    except HTTPError as error:
                        if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                            raise
                    except (URLError, TimeoutError, ConnectionError, IncompleteRead, ssl.SSLError) as error:
                        if _certificate_error(error) or attempt == 2:
                            raise
                    if cancel.wait(attempt + 1):
                        checkpoint(cancel)
        except (HTTPError, URLError, TimeoutError, ConnectionError, IncompleteRead, ssl.SSLError) as error:
            if _certificate_error(error) or source_index == len(sources) - 1:
                raise


def _certificate_error(error):
    return isinstance(error, ssl.SSLCertVerificationError) or isinstance(getattr(error, 'reason', None), ssl.SSLCertVerificationError)


def _download_once(artifact, cache, cancel, progress, opener, range_bytes=None, transport_url=None):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / artifact.filename
    if target.is_file() and target.stat().st_size == artifact.size and digest(target, cancel) == artifact.sha256:
        return target
    partial = target.with_name(target.name + '.part')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset >= artifact.size:
        # Only this exact task-owned artifact cache entry is replaced.
        partial.unlink()
        offset = 0
    headers = {'User-Agent': 'Kira-Android-Linux-Bootstrap', 'Accept-Encoding': 'identity'}
    end = min(artifact.size - 1, offset + range_bytes - 1) if range_bytes else artifact.size - 1
    if range_bytes:
        headers['Range'] = f'bytes={offset}-{end}'
    elif offset:
        headers['Range'] = f'bytes={offset}-'
    request = Request(transport_url or artifact.url, headers=headers)
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    if opener is None:
        deadline = time.monotonic() + _DOWNLOAD_REQUEST_SECONDS
        transport = build_opener(_DownloadHTTPSHandler(context, cancel, deadline), _DownloadRedirectHandler())
        open_response = transport.open(request, timeout=_DOWNLOAD_IDLE_SECONDS)
    else:
        open_response = opener(request, timeout=_DOWNLOAD_IDLE_SECONDS, context=context)
    with open_response as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('Runtime download redirected away from HTTPS')
        status = response.status
        if status == 206:
            expected = f'bytes {offset}-{end}/{artifact.size}'
            if (not offset and not range_bytes) or response.headers.get('Content-Range') != expected:
                raise ValueError('Runtime download returned an invalid byte range')
        elif status == 200:
            offset = 0
            end = artifact.size - 1
        else:
            raise ValueError(f'Unexpected runtime download status: {status}')
        mode = 'ab' if offset else 'wb'
        with partial.open(mode) as stream:
            read = getattr(response, 'read1', response.read)
            while True:
                checkpoint(cancel)
                chunk = read(min(CHUNK, end - offset + 2))
                checkpoint(cancel)
                if not chunk:
                    break
                offset += len(chunk)
                if offset > end + 1:
                    raise ValueError('Runtime download exceeded its pinned length')
                stream.write(chunk)
                progress(offset, artifact.size)
            stream.flush()
            os.fsync(stream.fileno())
    if offset != end + 1:
        raise ValueError('Runtime download is incomplete; retry will resume it')
    if offset != artifact.size:
        return None
    if digest(partial, cancel) != artifact.sha256:
        partial.unlink()
        raise ValueError('Runtime download SHA256 does not match the bundled manifest')
    checkpoint(cancel)
    os.replace(partial, target)
    return target


def confined(root, relative):
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError('Runtime archive path escapes its staging directory')
    return candidate


def member_path(name, strip_components=0):
    if '\\' in name or '\x00' in name:
        raise ValueError('Invalid runtime archive path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Runtime archive has an unsafe member path')
    parts = path.parts[strip_components:]
    return Path(*parts) if parts else None


def unpack_rootfs(archive, stage, cancel, *, strip_components=0,
                  max_bytes=MAX_ROOTFS_BYTES, max_files=MAX_ROOTFS_FILES):
    """Translate guest absolute symlinks into equivalent confined relative ones.

    Device nodes are supplied by PRoot binds. Setuid bits and archive ownership
    are not applied to the Android filesystem.
    """
    stage = Path(stage).resolve()
    stage.mkdir(parents=True, exist_ok=True)
    if any(stage.iterdir()):
        raise ValueError('Rootfs must be extracted into a new empty generation')
    total = count = 0
    hardlinks = []
    with tarfile.open(archive, 'r:*') as source:
        for member in source:
            checkpoint(cancel)
            count += 1
            total += max(0, member.size)
            if count > max_files or total > max_bytes:
                raise ValueError('Runtime archive exceeds installation limits')
            relative = member_path(member.name, strip_components)
            if relative is None:
                continue
            target = confined(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.issym():
                link = PurePosixPath(member.linkname)
                if '\\' in member.linkname or '\x00' in member.linkname:
                    raise ValueError('Invalid runtime symlink')
                destination = (stage / str(link).lstrip('/')) if link.is_absolute() else target.parent / str(link)
                if not destination.resolve().is_relative_to(stage):
                    raise ValueError('Runtime symlink escapes its generation')
                if target.is_symlink():
                    target.unlink()
                os.symlink(os.path.relpath(destination, target.parent), target)
            elif member.islnk():
                link = member_path(member.linkname, strip_components)
                if link is None:
                    raise ValueError('Invalid runtime hardlink')
                hardlinks.append((relative, link))
            elif member.isfile():
                if target.is_symlink():
                    target.unlink()
                with source.extractfile(member) as data, target.open('wb') as output:
                    copied = 0
                    while chunk := data.read(CHUNK):
                        checkpoint(cancel)
                        copied += len(chunk)
                        output.write(chunk)
                    if copied != member.size:
                        raise ValueError('Runtime archive contains a truncated file')
                os.chmod(target, (member.mode & 0o777) | 0o600)
            elif not (member.ischr() or member.isblk() or member.isfifo()):
                raise ValueError('Unsupported runtime archive member')
    for relative, link in hardlinks:
        checkpoint(cancel)
        target, source = confined(stage, relative), confined(stage, link)
        if not source.is_file() or source.is_symlink():
            raise ValueError('Runtime hardlink target is missing or is not a regular file')
        try:
            os.link(source, target)
        except OSError as error:
            if error.errno not in {errno.EPERM, errno.EACCES, errno.EXDEV, errno.EOPNOTSUPP}:
                raise
            total += source.stat().st_size
            if total > max_bytes:
                raise ValueError('Runtime hardlink copies exceed installation limits')
            with source.open('rb') as input, target.open('xb') as output:
                while chunk := input.read(CHUNK):
                    checkpoint(cancel)
                    output.write(chunk)
            shutil.copymode(source, target)
    return {'files': count, 'bytes': total}
