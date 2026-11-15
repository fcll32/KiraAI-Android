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

当前社区测试包为 v1.0.0，versionCode 21，适用于 Android 9 及以上的 ARM64 手机。首次启动需要联网准备运行环境。

## 下载与安装

在 [v1.0.0 code 21 Release](https://github.com/fcll32/KiraAI-Android/releases/tag/v1.0.0-code21) 下载 APK。

兼容包沿用旧版 0.6.3 测试包的签名，可以覆盖安装使用同一证书签名的版本。如果 Android 提示签名不一致，请保留已安装的应用，先核对其版本和签名证书。

APK 的 SHA-256：

338cad34a178d3771a296330ff4c866ab0ab19816b83d20f6cba3e99b725834d

## 源码与构建

Release 中的 APK 对应源码提交 [4f67d14](https://github.com/fcll32/KiraAI-Android/commit/4f67d14ccc28efaecbd76e6e2d48347fe34934bb)。仓库包含 Android 宿主、Python 运行时、测试代码和架构说明。

Android 工程使用 Android SDK 36、JDK 21、Gradle 9.2.1 和 Chaquopy 17.2.0。准备 APK 还需要项目文档所列的运行时归档和 KiraAI 官方种子。Git 仓库不包含本地运行时输入和生成的构建文件。

Windows 构建入口为 android-kira-host/build.ps1。手机包使用 -Abi arm64。构建与验收详情见 [ANDROID_LINUX_RUNTIME.md](ANDROID_LINUX_RUNTIME.md) 和 [ANDROID_HOST_REFACTOR_PLAN.md](ANDROID_HOST_REFACTOR_PLAN.md)。

## 上游项目

- [KiraAI](https://github.com/xxynet/KiraAI)
- [KiraAI 官方文档](https://github.com/KiraAI-Dev/kira-ai-docs)