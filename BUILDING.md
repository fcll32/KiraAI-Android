# Building the Android host / 构建手机版

## Status / 现状

The application source is published, but a fresh clone is **not yet a self-contained build environment**. Existing scripts require prepared runtime archives, manifests, toolchains and an upstream Git checkout. Pinned inputs are recorded in `build-inputs/`. Automated provisioning and a clean-machine build remain to be completed.

应用源码已公开，但新克隆的仓库**还不能直接一键构建**。现有脚本依赖预先准备的归档、清单、工具链和上游 Git 副本；固定输入见 `build-inputs/`，自动准备流程及干净环境构建仍待补齐。

## External workspace / 外部工作目录

On this maintainer's machine, tools, dependencies, caches, temporary files and build outputs must be under `D:\BaiduNetdiskDownload\kira这是缓存之类` (WSL: `/mnt/d/BaiduNetdiskDownload/kira这是缓存之类`). Build only in an external copy there. `.gitignore` does not permit creating generated files in the authoritative source checkout.

本机所有测试和构建资料必须放入上述固定根目录。既有 `Prepare-KiraTestWorkspace.ps1` 默认同步原项目，是本机工具，不是此公开仓库的新机器安装器。

Set `TEMP`, `TMP`, `TMPDIR`, `XDG_CACHE_HOME`, `PIP_CACHE_DIR`, `npm_config_cache`, `GRADLE_USER_HOME`, `ANDROID_USER_HOME`, `ANDROID_AVD_HOME` and `PYTHONPYCACHEPREFIX` to subdirectories of that root before running anything; set `PYTHONDONTWRITEBYTECODE=1`. Browser downloads/profiles must use that root too. Stop if it is unavailable.

## Prerequisites / 前置输入

The root of the external build copy must contain `android-kira-host/` and this prepared layout, as expected by `build.ps1` and `prepare_assets.py`:

```text
.runtime/
  android-sdk/                       # Android SDK platform 36 and build tools
  jdk21/jdk-21.0.12.1+1/              # JDK 21
  gradle-9.2.1/                      # no Gradle wrapper is shipped
  gradle-home/
  android-python-probe/
    cross-tools/python311/python.exe
    android-home/                   # private signing inputs
  android-linux-runtime/artifacts/
    native-runtime-manifest.json
    node-runtime-manifest.json
    pip-progress-manifest.json
    aarch64/extracted/...
    x86_64/extracted/...
    arm64v8/debian-bookworm-slim-rootfs.tar.gz
    amd64/debian-bookworm-slim-rootfs.tar.gz
    node/...
    pip/...
    licenses/...
  official-kira/
    v3.1.2/                         # pinned Git checkout
    artifacts/dist-v3.1.2.zip
```

- Gradle declares Android Gradle Plugin `9.0.1`, Chaquopy `17.2.0`, Python `3.11` and `certifi==2026.10.23`.
- KiraAI seed: `v3.1.2`, commit `89013745ba3281bdc248e704bbcb619417cf4f57`. The Git checkout must be at this commit. The frontend ZIP must be 2,232,346 bytes, SHA-256 `eba1639b7e7c7ca8089b8e3beb51be93c555cb42f19d6e51e40f9f9676affe09`.
- Copy the three runtime manifests from `build-inputs/` into the external artifacts directory. Native `source` paths are relative to the external build-copy root; run packaging from that root. Other manifests record upstream URLs and checksums.
- `prepare_assets.py` currently requires **both ARM64 and x86_64 inputs**, even for an ARM64-only APK.
- Runtime source archives and Termux recipes are available as a Release attachment; this attachment does not include SDKs or prebuilt runtime payloads. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Build / 构建

After preparing the inputs, run from the external build-copy root:

```powershell
# Development build; the default BuildType is debug.
.\android-kira-host\build.ps1 -Abi arm64

# Release build; requires private signing configuration.
.\android-kira-host\build.ps1 -Abi arm64 -BuildType release
```

Provide `ORG_GRADLE_PROJECT_hostReleaseKeystore`, `ORG_GRADLE_PROJECT_hostReleaseStorePassword`, `ORG_GRADLE_PROJECT_hostReleaseKeyPassword` and optionally `ORG_GRADLE_PROJECT_hostReleaseKeyAlias` through the process environment. Without these inputs the release APK is unsigned. Never commit keys/passwords. A new key cannot update an installation signed with the published certificate.

脚本默认生成 debug 包；正式签名通过进程环境变量提供。缺少签名配置时 release 包未签名，自建密钥不能覆盖发布者签名的安装。

Outputs are under `android-kira-host/app/build/outputs/apk/` in the external copy. Before publishing to Release, check package ID, version, ABI, signature, size, SHA-256 and the APK/source relationship. Physical-device acceptance is a separate check.

## Code 22 publication build / 本次打包

Code 22 was compiled using AAPT2, javac and D8 with the checksum-bound Chaquopy dependencies retained from the earlier build. Current host Java and Python modules were rebuilt, then aligned, signed and verified. Windows Gradle native-library initialization failed in this environment; a clean Gradle build remains pending. The Release build-records attachment includes the assembly/signing scripts and per-file source binding used for this package.
