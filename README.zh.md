<div align="center">

# KiraAI 手机版

面向 ARM64 手机的 KiraAI 社区版 Android 宿主。

[English](README.md) | [简体中文](README.zh.md)

[手机版源码](https://github.com/fcll32/KiraAI-Android/tree/main/android-kira-host) · [发行下载](https://github.com/fcll32/KiraAI-Android/releases) · [KiraAI 文档](https://docs.kira-ai.top)

</div>

## 功能

- 在 Android 手机上通过 Debian PRoot 环境运行 KiraAI。
- 在同一个 Android 应用内提供 KiraAI 和 NapCat 面板。
- 提供备份管理、运行状态查看和可选的本机 NapCat 支持。

当前社区正式发布版本为 v1.0.1，versionCode 22，适用于 Android 9 及以上的 ARM64 手机。首次启动需要联网准备运行环境。验收范围与待办见[发布状态](RELEASE_STATUS.md)。

## 下载与安装

在 [v1.0.1 code 22 Release](https://github.com/fcll32/KiraAI-Android/releases/tag/v1.0.1-code22) 下载 APK。

新版沿用 code 21 和旧版 0.6.3 兼容包的签名，可以覆盖安装使用同一证书签名的版本。如果 Android 提示签名不一致，请保留已安装的应用，先核对其版本和签名证书。

APK 的 SHA-256：

```text
8a08b09988bcc297243f96c40fa9089d6d5b339507980f7d65dcd8671c8e4f62
```

Release 同时提供 `SHA256SUMS.txt` 和运行时依赖源码归档。APK 作为 Release 附件分发。

### 应用内更新

v1.0.1 已内置下方公开地址，且会迁移旧版保存的官方更新地址，保留自定义源。code 21 用户可先把应用更新弹窗的 **APK 更新清单（HTTPS）** 改为下方地址，以发现新版：

```text
https://raw.githubusercontent.com/fcll32/KiraAI-Android/main/android-host-update.json
```

点击“检查更新”保存设置。已安装 code 22 时会提示当前版本不低于已发布版本；也可从 Release 下载后，在应用中选择本地 APK。

## 源码与构建

Release 中的 APK 对应[发布源码标签](https://github.com/fcll32/KiraAI-Android/tree/v1.0.1-code22)。仓库包含 Android 宿主、Python 运行时、测试代码和架构说明。

Android 工程使用 Android SDK 36、JDK 21、Gradle 9.2.1 和 Chaquopy 17.2.0。准备 APK 还需要项目文档所列的运行时归档和 KiraAI 官方种子。Git 仓库不包含本地运行时输入和生成的构建文件。

前置输入、外部构建目录与签名说明见 [BUILDING.md](BUILDING.md)。Windows 入口为 `android-kira-host/build.ps1 -Abi arm64`，默认生成 debug 包；新克隆的仓库目前还不能直接一键构建。[ANDROID_LINUX_RUNTIME.md](ANDROID_LINUX_RUNTIME.md) 和 [ANDROID_HOST_REFACTOR_PLAN.md](ANDROID_HOST_REFACTOR_PLAN.md) 保留架构与历史验收记录。

## 许可证

宿主原创源码采用 [MIT 许可证](LICENSE)。KiraAI 本体及其他第三方组件保留各自许可证；说明和许可原文见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 上游项目

- [KiraAI](https://github.com/xxynet/KiraAI)
- [KiraAI 官方文档](https://github.com/KiraAI-Dev/kira-ai-docs)
