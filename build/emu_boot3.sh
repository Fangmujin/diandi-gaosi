#!/usr/bin/env bash
# Cold boot the emulator and leave it running. Prints "READY" once booted.
# 磁盘版：AVD 与临时目录搬到 D 盘（C 盘长期 100% 满）。
# 附加 -shell-serial：guest 侧 root shell 经 TCP 暴露，用于手工注入 adb 公钥
# （google_apis_playstore 是 user 版，强制 USB 调试授权，headless 下没法点对话框）。
set -u
ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native"
EMULATOR="C:/Users/28928/AppData/Local/Android/Sdk/emulator/emulator.exe"
export ANDROID_USER_HOME="D:/android-home"
export ANDROID_AVD_HOME="D:/android-home/avd"
export TEMP="D:/tmp"
export TMP="D:/tmp"
export TMPDIR="D:/tmp"
export MSYS_NO_PATHCONV=1
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"

mkdir -p "D:/tmp"
powershell -NoProfile -Command "Get-Process -Name qemu*,emulator*,netsimd* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"
sleep 5
cd "$ROOT" || exit 1

"$EMULATOR" -avd Medium_Phone_API_36.1 -no-window -no-audio -no-boot-anim \
  -gpu host -no-snapshot-save -no-snapshot-load \
  -no-metrics -crash-report-mode disabled \
  -skip-adb-auth \
  -shell-serial tcp:127.0.0.1:5557,server,nowait \
  > emulator_headless.out 2> emulator_headless.err &

sleep 2
echo "SHELL_SERIAL: 127.0.0.1:5557"

$ADB kill-server >/dev/null 2>&1
sleep 2
$ADB start-server >/dev/null 2>&1
for _ in $(seq 1 45); do
  sleep 8
  S="$($ADB devices 2>&1 | grep emulator | tr -d '\r')"
  if echo "$S" | grep -q "device$"; then
    B="$($ADB shell getprop sys.boot_completed 2>&1 | tr -d '\r')"
    if [ "$B" = "1" ]; then echo "READY: $S"; exit 0; fi
  fi
done
echo "ADB NOT AUTHORIZED (see emulator_headless.out)"
$ADB devices | tr -d '\r'
exit 1
