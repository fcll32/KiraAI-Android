# Third-party notices / 第三方说明

These notices describe the native Android host and the v1.0.0 code 21 APK. License texts copied from that APK are in [third-party/licenses](third-party/licenses/). They are preserved verbatim and apply to their respective components. This file does not assign a license to the host's original code.

本文对应当前原生 Android 宿主和 code 21 APK。许可原文从实际发布包提取并保留；旧版 uni-app/DCloud 宿主的依赖说明不适用于这个工程。

| Component | Source / notices |
| --- | --- |
| KiraAI v3.1.2 seed | [Pinned upstream source](https://github.com/KiraAI-Dev/KiraAI/tree/89013745ba3281bdc248e704bbcb619417cf4f57); the APK contains the original source seed, including LICENSE and EULA. Copies: `third-party/licenses/KiraAI-AGPL-3.0.txt` and `KiraAI-EULA.md`. |
| PRoot 5.1.107.102 | [Termux source](https://github.com/termux/proot/tree/v5.1.107.102); `PRoot-GPL-2.0.txt`. |
| talloc 2.5.0 | [Samba source](https://download.samba.org/pub/talloc/talloc-2.5.0.tar.gz); `libtalloc-LGPL-3.0.txt` and `GPL-3.0.txt`. |
| libandroid-shmem 0.7 | [Termux source](https://github.com/termux/libandroid-shmem/tree/v0.7); `libandroid-shmem-copyright.txt`. |
| Node.js v24.22.2 and bundled dependencies | [Node.js source](https://github.com/nodejs/node/tree/v24.22.2); `Node-LICENSE-official.txt` contains upstream component notices. |
| pip 26.3 | [pip source](https://github.com/pypa/pip/tree/26.3); `pip-LICENSE.txt`. Additional bundled notices remain in the official wheel inside the APK seed. |
| Debian Bookworm root filesystem | Pinned Debuerreotype URLs/checksums: `build-inputs/bootstrap-manifest.json`. Package copyright files remain under `/usr/share/doc/` in the rootfs. |
| Chaquopy 17.2.0 / Python 3.11 | Declared in Gradle and `android-kira-host/apk-native-profiles.json`; [Chaquopy source](https://github.com/chaquo/chaquopy) and [Python licensing](https://docs.python.org/3/license.html). |
| Cursor swarm onboarding asset | `android-kira-host/app/src/main/assets/onboarding/cursor-swarm-LICENSE.txt`, MIT, copyright ZhengLinLei. |

The [release](https://github.com/fcll32/KiraAI-Android/releases/tag/v1.0.0-code21) provides `KiraAI-Android-v1.0.0-runtime-sources.zip`: PRoot, talloc and libandroid-shmem source archives, license texts and recorded Termux recipes. Archive identities are in `build-inputs/corresponding-source-archives.json`; recipe identities are in `build-inputs/termux-recipes/EVIDENCE.json`. The recipe snapshot is not proof that those exact recipes/toolchains produced the downloaded binaries.

NapCat and Linux QQ are optional downloads installed at runtime; their upstream notices and terms remain applicable. They are not part of this host source checkout.

This inventory is not a claim that a complete dependency/license audit or reproducible build has finished. Remaining work is tracked in [RELEASE_STATUS.md](RELEASE_STATUS.md).
