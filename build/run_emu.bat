@echo off
set ANDROID_USER_HOME=D:\android-home
set ANDROID_AVD_HOME=D:\android-home\avd
set TEMP=D:\tmp
set TMP=D:\tmp
set ANDROID_SDK_HOME=C:\Users\28928
cd /d C:\Users\28928\WorkBuddy\2026-10-11-15-32-41\diandi-gaosi\native
"C:\Users\28928\AppData\Local\Android\Sdk\emulator\emulator.exe" -avd Medium_Phone_API_36.1 -no-window -no-audio -no-boot-anim -gpu host -no-snapshot-save -no-snapshot-load -no-metrics -crash-report-mode disabled -skip-adb-auth -partition-size 1024 > emulator_headless.out 2> emulator_headless.err
