"""Domestic package defaults and narrowly scoped, integrity-preserving transports."""
import re

PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
NPM_REGISTRY = 'https://registry.npmmirror.com'
APT_SOURCES = (
    'Types: deb\nURIs: https://mirrors.tuna.tsinghua.edu.cn/debian\n'
    'Suites: bookworm bookworm-updates\nComponents: main\n'
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n'
    'Types: deb\nURIs: https://mirrors.tuna.tsinghua.edu.cn/debian-security\n'
    'Suites: bookworm-security\nComponents: main\n'
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n')


def download_urls(url):
    """Only known public runtime artifacts use an accelerator; identity stays upstream."""
    node = re.fullmatch(r'https://nodejs\.org/download/release/(v\d+\.\d+\.\d+)/(node-v\d+\.\d+\.\d+-linux-(?:arm64|x64)\.tar\.xz)', url)
    if node:
        return ('https://npmmirror.com/mirrors/node/' + node[1] + '/' + node[2], url)
    allowed = (
        r'https://raw\.githubusercontent\.com/debuerreotype/docker-debian-artifacts/[0-9a-f]{40}/bookworm/slim/oci/blobs/rootfs\.tar\.gz',
        r'https://raw\.githubusercontent\.com/xiwangly2/NapCat-Installer/[0-9a-f]{40}/script/install\.sh',
        r'https://github\.com/xxynet/NapCatQQ/releases/download/v\d+\.\d+\.\d+/NapCat\.Shell\.zip',
    )
    if any(re.fullmatch(pattern, url) for pattern in allowed):
        return ('https://gh-proxy.com/' + url, url)
    return (url,)
