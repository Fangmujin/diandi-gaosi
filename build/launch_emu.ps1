# 通过 WMI Win32_Process.Create 启动 run_emu.bat。
# 关键点：新进程的父进程是 wmiprvse.exe，不受我们这条命令的进程树回收影响。
$ErrorActionPreference = "SilentlyContinue"
$adb = "C:\Users\28928\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$bat = "C:\Users\28928\WorkBuddy\2026-10-11-15-32-41\diandi-gaosi\build\run_emu.bat"
$root = "C:\Users\28928\WorkBuddy\2026-10-11-15-32-41\diandi-gaosi\native"

Get-Process -Name qemu*, emulator*, netsimd* | Stop-Process -Force
Start-Sleep -Seconds 4

$avd = "D:\android-home\avd\Medium_Phone.avd"
$p = Join-Path $avd "multiinstance.lock"
if (Test-Path $p) { [System.IO.File]::Delete($p) }
$lk = Join-Path $avd "hardware-qemu.ini.lock"
if (Test-Path $lk) { [System.IO.Directory]::Delete($lk, $true) }
New-Item -ItemType Directory -Path "D:\tmp" -Force | Out-Null

$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c `"$bat`"" }
Write-Output ("CREATE_RC=" + $r.ReturnValue + " PID=" + $r.ProcessId)

Start-Sleep -Seconds 10
$q = Get-Process -Name qemu-system-x86_64-headless -ErrorAction SilentlyContinue
Write-Output ("qemu=" + $(if ($q) { $q.Id } else { "none" }))
Get-Content (Join-Path $root "emulator_headless.out") -Tail 4
Start-Sleep -Seconds 2
& $adb kill-server
Start-Sleep -Seconds 2
& $adb start-server
Write-Output "ADB_READY"
