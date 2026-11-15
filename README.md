<div align="center">

# KiraAI for Android

A community Android host for running KiraAI on ARM64 phones.

[English](README.md) | [简体中文](README.zh.md)

[Android source](https://github.com/fcll32/KiraAI-Android/tree/main/android-kira-host) · [Releases](https://github.com/fcll32/KiraAI-Android/releases) · [KiraAI documentation](https://docs.kira-ai.top)

</div>

## What it does

- Runs KiraAI locally on an Android phone using a Debian PRoot environment.
- Provides the KiraAI and NapCat panels from one Android app.
- Includes backup management, runtime status, and optional local NapCat support.

This is a community test build. The current ARM64 package is version 1.0.0, version code 21, and requires Android 9 or later. First launch needs an internet connection to prepare the runtime.

## Download and install

Download the APK from the [v1.0.0 code 21 release](https://github.com/fcll32/KiraAI-Android/releases/tag/v1.0.0-code21).

The compatibility APK uses the same signing certificate as the older 0.6.3 test build. It can update an installation signed with that certificate. If Android reports a signature mismatch, keep the installed app and check its version and signing certificate before changing it.

APK SHA-256:

338cad34a178d3771a296330ff4c866ab0ab19816b83d20f6cba3e99b725834d

## Source and build

The release APK corresponds to source commit [4f67d14](https://github.com/fcll32/KiraAI-Android/commit/4f67d14ccc28efaecbd76e6e2d48347fe34934bb). The repository contains the Android host, its Python runtime, tests, and architecture notes.

The Android project uses Android SDK 36, JDK 21, Gradle 9.2.1, and Chaquopy 17.2.0. APK preparation also needs the runtime archives and official KiraAI seed described in the project documentation. Local runtime inputs and generated build files are excluded from Git.

The Windows build entry point is android-kira-host/build.ps1. Use -Abi arm64 for an ARM64 phone package. See [ANDROID_LINUX_RUNTIME.md](ANDROID_LINUX_RUNTIME.md) and [ANDROID_HOST_REFACTOR_PLAN.md](ANDROID_HOST_REFACTOR_PLAN.md) for build and validation details.

## Upstream projects

- [KiraAI](https://github.com/xxynet/KiraAI)
- [KiraAI documentation](https://github.com/KiraAI-Dev/kira-ai-docs)