param([ValidateSet('universal','arm64')][string]$Abi = 'universal', [string]$PythonCommand,
      [ValidateSet('debug','release')][string]$BuildType = 'debug')
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (!$PythonCommand) { $PythonCommand = Join-Path $taskRoot '.runtime/android-python-probe/cross-tools/python311/python.exe' }
& $PythonCommand (Join-Path $PSScriptRoot 'prepare_assets.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:JAVA_HOME = Join-Path $taskRoot '.runtime/jdk21/jdk-21.0.12.1+1'
$env:ANDROID_HOME = Join-Path $taskRoot '.runtime/android-sdk'
$env:GRADLE_USER_HOME = Join-Path $taskRoot '.runtime/gradle-home'
$env:ANDROID_USER_HOME = Join-Path $taskRoot '.runtime/android-python-probe/android-home'
$env:JAVA_TOOL_OPTIONS = "-Djdk.net.unixdomain.tmpdir=$($taskRoot.Replace('\','/'))/.runtime/java-tmp -Djava.io.tmpdir=$($taskRoot.Replace('\','/'))/.runtime/java-tmp"
$taskAbis = if ($Abi -eq 'arm64') { 'arm64-v8a' } else { 'arm64-v8a,x86_64' }
# Release signing: set ORG_GRADLE_PROJECT_hostReleaseKeystore, ..._hostReleaseStorePassword and ..._hostReleaseKeyPassword
# (optionally ..._hostReleaseKeyAlias) before running; without them the release APK is left unsigned.
$task = if ($BuildType -eq 'release') { ':app:assembleRelease' } else { ':app:assembleDebug' }
& (Join-Path $taskRoot '.runtime/gradle-9.2.1/bin/gradle.bat') -p $PSScriptRoot "-PhostPython=$PythonCommand" "-PhostAbis=$taskAbis" $task --console=plain --no-daemon 2>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "build/build-$Abi-$BuildType.log")
exit $LASTEXITCODE
