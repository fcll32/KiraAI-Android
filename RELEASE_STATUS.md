# Release audit / 发布核对

Updated on 2026-11-16 (Asia/Shanghai).

## v1.0.1 / code 22

- Application ID: `top.kira.pythonprobe`; ARM64; Android 9+; release build with `debuggable=false`.
- APK: `KiraAI-Host-v1.0.1-code22-arm64.apk`, 84,158,655 bytes.
- SHA-256: `8a08b09988bcc297243f96c40fa9089d6d5b339507980f7d65dcd8671c8e4f62`.
- Signing certificate SHA-256: `9dc5322c1ffb538ace6202800707c7231aa304e1dce422f46f26e8b7ce76039f`. Matches the published code 21 APK and the 0.6.3 compatibility line.
- Latest Android host source includes panel day/night synchronization and portrait configuration, plus corrected update/project URLs and migration of the retired saved official update URL. Custom sources are preserved.
- Fixed UTF-8 manifest reads in asset preparation and APK verification so Windows Chinese paths are decoded correctly.
- Checks passed: 230 Linux Python host regressions; 7 Java update transport/cache checks; APK CRC; all 13 expected native libraries and ELF alignment; all 566 upstream Kira seed files; ABI, package ID, version, signature (including Android 9) and ZIP alignment.
- Built with AAPT2, javac, D8 and the existing checksum-bound Chaquopy/Python dependencies. All current host Java and Python modules were rebuilt. Windows Gradle initialization remains blocked by native-library loading in this environment, so this is not a clean Gradle build.
- Application source is included in tag [`v1.0.1-code22`](https://github.com/fcll32/KiraAI-Android/tree/v1.0.1-code22). `build-info.json`, checksums and source preparation records accompany this release.
- code 22 physical-device acceptance has not been reported. code 21's result is recorded separately below.

## v1.0.0 / code 21

- **The user confirmed physical-device acceptance on 2026-11-16.** This is user-provided validation; no device model or detailed checklist was supplied. Do not replace it with an untested claim, or extend it to code 22.
- Original APK SHA-256: `338cad34a178d3771a296330ff4c866ab0ab19816b83d20f6cba3e99b725834d`.
- Source import: `4f67d14ccc28efaecbd76e6e2d48347fe34934bb`; publication metadata corrected in `ecdac5d6a2e72eb243be0172f869d2e8d7442bb0`. The code 21 tag points to that correction; existing clones with the original download-only tag may need to refresh it.

## Remaining work / 待办

1. **Clean build:** automatic toolchain/input provisioning and a clean-machine Gradle build remain incomplete. Current dependency sources/recipes are published, but their exact binary build/toolchain correspondence remains unverified. See [BUILDING.md](BUILDING.md).
2. **Code 22 device validation:** install/upgrade and runtime acceptance for this new build remain to be confirmed; code 21 has passed per the user.
3. **Upstream documentation:** [PR #9](https://github.com/KiraAI-Dev/kira-ai-docs/pull/9) allows maintainer edits; upstream Vercel preview authorization and merging are controlled by the upstream team.

宿主原创源码采用 MIT，第三方保留各自许可证。code 21 真机已通过（用户确认）；code 22 已完成上述构建与回归验证，真机结果独立记录。干净环境 Gradle 构建及上游预览授权仍待完成。
