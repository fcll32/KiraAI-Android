import hashlib
import io
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from domestic_sources import APT_SOURCES, PIP_INDEX_URL, NPM_REGISTRY, download_urls
from linux_installer import Artifact, download


class Reply(io.BytesIO):
    def __init__(self, body, url, status=200, headers=None):
        super().__init__(body)
        self.url, self.status, self.headers = url, status, headers or {}
    def geturl(self): return self.url


class DomesticSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.cancel = threading.Event()
        self.url = 'https://github.com/xxynet/NapCatQQ/releases/download/v4.9.43/NapCat.Shell.zip'
        self.body = b'abcdef'
        self.artifact = Artifact('shell.zip', self.url, hashlib.sha256(self.body).hexdigest(), len(self.body))

    def test_domestic_defaults_keep_https_and_debian_signature_verification(self):
        self.assertEqual(APT_SOURCES.count('Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg'), 2)
        self.assertIn('https://mirrors.tuna.tsinghua.edu.cn/debian-security', APT_SOURCES)
        self.assertEqual(PIP_INDEX_URL, 'https://pypi.tuna.tsinghua.edu.cn/simple')
        self.assertEqual(NPM_REGISTRY, 'https://registry.npmmirror.com')
        self.assertNotIn('trusted=yes', APT_SOURCES)

    def test_only_known_public_artifacts_are_forwarded(self):
        self.assertEqual(download_urls(self.url)[-1], self.url)
        unknown = 'https://example.test/private?token=not-forwarded'
        self.assertEqual(download_urls(unknown), (unknown,))
        node = 'https://nodejs.org/download/release/v24.22.2/node-v24.22.2-linux-arm64.tar.xz'
        self.assertTrue(download_urls(node)[0].startswith('https://npmmirror.com/'))

    def test_mirror_failure_resumes_from_official_without_changing_artifact_identity(self):
        (self.cache / 'shell.zip.part').write_bytes(b'ab')
        requests = []
        def opener(request, **kwargs):
            requests.append(request.full_url)
            if request.full_url != self.url:
                raise HTTPError(request.full_url, 404, 'unavailable mirror', None, None)
            self.assertEqual(request.get_header('Range'), 'bytes=2-')
            return Reply(b'cdef', self.url, 206, {'Content-Range': 'bytes 2-5/6'})
        result = download(self.artifact, self.cache, self.cancel, opener=opener)
        self.assertEqual(result.read_bytes(), self.body)
        self.assertEqual(requests, list(download_urls(self.url)))
        self.assertEqual(self.artifact.url, self.url)

    def test_mirror_content_still_must_match_pinned_digest(self):
        def opener(request, **kwargs): return Reply(b'123456', request.full_url)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            download(self.artifact, self.cache, self.cancel, opener=opener)
        self.assertFalse((self.cache / 'shell.zip').exists())
        self.assertFalse((self.cache / 'shell.zip.part').exists())

    def test_certificate_failure_is_not_bypassed_by_fallback(self):
        calls = []
        def opener(request, **kwargs):
            calls.append(request.full_url)
            raise ssl.SSLCertVerificationError('untrusted test certificate')
        with self.assertRaises(ssl.SSLCertVerificationError):
            download(self.artifact, self.cache, self.cancel, opener=opener)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__': unittest.main()
