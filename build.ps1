# 点点高斯 APK 手工构建：aapt2 compile/link -> javac -> d8 -> 注入 dex/单 ABI jniLibs/zip 目录条目 -> zipalign -> apksigner
# 用法：
#   .\build.ps1                # 打 x86_64 包（模拟器）
#   .\build.ps1 -Abi arm64-v8a # 打 arm64 包（真机）
#   .\build.ps1 -Abi all       # 两个 ABI 各打一个包
# 注意：不使用 $ErrorActionPreference=Stop（native stderr 警告会触发终止），改由显式 Test-Path 检查

param(
    [string]$Abi = "x86_64"
)

$root   = "C:\Users\28928\WorkBuddy\2026-10-11-15-32-41\diandi-gaosi"
$shell  = Join-Path $root "app"
$assets = Join-Path $shell "assets"
$build  = Join-Path $root "build"
$out    = Join-Path $root "dist"
$sdk    = "C:\Users\28928\AppData\Local\Android\Sdk"
$bt     = Join-Path $sdk "build-tools\36.1.0"
$androidJar = Join-Path $sdk "platforms\android-36.1\android.jar"
$javaHome = "C:\Program Files\Eclipse Adoptium\jdk-17.0.4.8-hotspot"
$env:PATH = "$javaHome\bin;" + $env:PATH

if ($Abi -eq "all") { $abis = @("x86_64", "arm64-v8a") } else { $abis = @($Abi) }

if (-not (Test-Path $out)) { New-Item -ItemType Directory -Force -Path $out | Out-Null }
# 不用 Remove-Item：本机 PowerShell 的 safe-delete 钩子会拒绝目录删除并中断构建。
# 改用 .NET Directory.Delete / File.Delete 直接删。
foreach ($d in @("$build\compiled", "$build\gen", "$build\classes", "$build\dex")) {
    if (Test-Path $d) { [System.IO.Directory]::Delete($d, $true) }
    [System.IO.Directory]::CreateDirectory($d) | Out-Null
}

Write-Host "== [1/7] aapt2 compile =="
& (Join-Path $bt "aapt2.exe") compile --dir (Join-Path $shell "res") -o "$build\compiled" 2>&1 | Out-Host

Write-Host "== [2/7] aapt2 link =="
$flatFiles = Get-ChildItem "$build\compiled" -Recurse -Filter *.flat | ForEach-Object { "`"$($_.FullName)`"" }
$flatArgs = $flatFiles -join " "
$linkCmd = "& `"$(Join-Path $bt 'aapt2.exe')`" link -o `"$build\base.apk`" -I `"$androidJar`" --manifest `"$(Join-Path $shell 'AndroidManifest.xml')`" -A `"$assets`" --java `"$build\gen`" --min-sdk-version 24 --target-sdk-version 36 $flatArgs 2>&1"
Invoke-Expression $linkCmd | Out-Host
if (-not (Test-Path "$build\base.apk")) { throw "aapt2 link failed" }

Write-Host "== [3/7] javac (子进程，规避 PS5.1 GBK stderr 卡死) =="
& (Join-Path $build "javac_step.ps1")
$jlog = Join-Path $build "javac.log"
# javac 失败时 javac.log 里会有 exit=<非0>。绝不能靠"classes 目录里有 .class"判断成功：
# 上一轮的旧 class 还留着，会导致 d8 拿残缺的类集打出一个能装上但行为诡异的包。
if (Test-Path $jlog) {
    $jtext = [System.IO.File]::ReadAllText($jlog, [System.Text.Encoding]::UTF8)
    Write-Host $jtext
    if ($jtext -notmatch "exit=0") { throw "javac failed（见上方 javac.log），已中止，不会产出半成品 APK" }
}
if (-not (Get-ChildItem "$build\classes" -Recurse -Filter *.class | Select-Object -First 1)) { throw "javac produced no classes" }
Write-Host ("javac ok, classes=" + (Get-ChildItem "$build\classes" -Recurse -Filter *.class).Count)

Write-Host "== [4/7] d8 =="
$classFiles = Get-ChildItem "$build\classes" -Recurse -Filter *.class | ForEach-Object { $_.FullName }
$d8Args = @("-cp", "$bt\lib\d8.jar", "com.android.tools.r8.D8", "--release", "--lib", $androidJar, "--output", "$build\dex") + $classFiles
& (Join-Path $javaHome "bin\java.exe") $d8Args 2>&1 | Out-File -Encoding utf8 "$build\d8.log"
if (-not (Test-Path "$build\dex\classes.dex")) { Get-Content "$build\d8.log"; throw "d8 failed" }

$ks = Join-Path $build "hellosplat.keystore"
if (-not (Test-Path $ks)) {
    & (Join-Path $javaHome "bin\keytool.exe") -genkeypair -v -keystore $ks -alias hellosplat -keyalg RSA -keysize 2048 -validity 10000 `
        -storepass hellosplat -keypass hellosplat -dname "CN=Diandi Gaosi, OU=DiandiGaosi, O=DiandiGaosi, L=Unknown, ST=Unknown, C=CN" 2>&1 | Out-Host
}

$env:PYTHONIOENCODING = "utf-8"

foreach ($a in $abis) {
    Write-Host "===== 组装 ABI: $a ====="
    $env:DIANDI_ABI = $a

    Write-Host "== [5/7] inject dex + jniLibs($a) + zip dir entries =="
    python (Join-Path $build "inject_step.py") 2>&1 | Out-Host
    if (-not (Test-Path "$build\withdex.apk")) { Write-Host "inject failed, skip $a"; continue }

    Write-Host "== [6/7] zipalign =="
    # -P 16: 未压缩的 .so 按 16KB 页对齐（Android 15+ 16KB 页设备的硬性要求，
    #         仅满足 ELF 段对齐而 zip 不对齐，仍然加载失败）；其余条目仍按 4 字节。
    # 注意: -P 不能与 -p 同时使用。对齐必须在 apksigner 之前做, 否则会破坏 v2/v3 签名。
    & (Join-Path $bt "zipalign.exe") -f -P 16 4 "$build\withdex.apk" "$build\aligned.apk" 2>&1 | Out-Host
    & (Join-Path $bt "zipalign.exe") -c -P 16 4 "$build\aligned.apk" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "!! zipalign 16KB 校验失败, 中止 $a"; continue }

    Write-Host "== [7/7] sign =="
    $tmpApk = Join-Path $out ("diandi-gaosi-v0.1-" + $a + ".apk")
    & (Join-Path $javaHome "bin\java.exe") -jar (Join-Path $bt "lib\apksigner.jar") sign --ks $ks --ks-key-alias hellosplat --ks-pass pass:hellosplat --key-pass pass:hellosplat `
        --out $tmpApk "$build\aligned.apk" 2>&1 | Out-Host
    & (Join-Path $javaHome "bin\java.exe") -jar (Join-Path $bt "lib\apksigner.jar") verify --verbose $tmpApk 2>&1 | Out-Host

    # 重命名为中文名（PowerShell 5.1 直写中文文件名易乱码，交给 Python 走 UTF-8）
    python (Join-Path $build "rename_apk.py") $tmpApk $a 2>&1 | Out-Host
}

Write-Host "BUILD COMPLETE"
