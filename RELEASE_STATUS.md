# Release audit / 发布核对

Checked on 2026-11-16 (Asia/Shanghai).

## v1.0.0 / code 21

- Application ID: `top.kira.pythonprobe`; ARM64; Android 9+ (minSdk 28).
- APK: `KiraAI-Host-v1.0.0-code21-arm64-legacy-signature.apk`, 84,158,655 bytes.
- SHA-256: `338cad34a178d3771a296330ff4c866ab0ab19816b83d20f6cba3e99b725834d`.
- Signing certificate SHA-256: `9dc5322c1ffb538ace6202800707c7231aa304e1dce422f46f26e8b7ce76039f` (same as the 0.6.3 compatibility line).
- Application source was imported in `4f67d14ccc28efaecbd76e6e2d48347fe34934bb`. This audit changes publication metadata, dependency manifests and documentation, not application code.
- The host's original source code is licensed under MIT. Third-party components retain their own licenses.
- Against the retained build-source record, 67 of 68 files match byte for byte; `runtime/host_runtime.py` differs only by one trailing blank line. No functional source difference was found in that comparison.
- The release tag originally pointed to download-only commit `ce8962d77059a9ddfb0566632adbe2e25b84e208`. The corrected tag points to this audit revision so GitHub's source archives include the application and notices. APK bytes are unchanged. Existing clones may need to refresh the tag.

## Remaining work / 待办

1. **Clean-machine build:** pinned manifests are included; automatic provisioning and a clean build remain incomplete. Recorded Termux recipes match package versions; their exact binary build/toolchain correspondence is unverified. See [BUILDING.md](BUILDING.md).
2. **Embedded update URL:** code 21's default manifest in the old repository returns 404. Existing users can switch to the [new manifest](https://raw.githubusercontent.com/fcll32/KiraAI-Android/main/android-host-update.json). The embedded default and About-page link need changing in a future APK build.
3. **Device acceptance:** code 21 has not completed physical-device install/upgrade and long-running Kira/NapCat acceptance. Older-version test records are historical evidence.
4. **Documentation PR:** [PR #9](https://github.com/KiraAI-Dev/kira-ai-docs/pull/9) is open, has no merge conflict and allows maintainer edits. Its Vercel preview requires upstream-team authorization; CodeRabbit is skipped. These are not passing deployment checks.

干净环境构建、APK 内置旧地址、当前版本真机验收及上游 Vercel 授权仍按以上条目跟进。正式发布状态不等于这些待办已经完成。
