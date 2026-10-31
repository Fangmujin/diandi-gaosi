# 模拟器端 APK 冒烟验证日志（第二轮 / emu-smoke2）

- 时间：2026-10-15
- 执行者：emu-smoke2
- 目标：在没有真机的情况下，用 x86_64 模拟器做设备级「外壳」验证：APK 能装、能启动、四个页面能渲染。
- 纪律：每完成一步立即追加写盘，避免中途中断导致零产出。

## 环境

- emulator: `C:\Users\28928\AppData\Local\Android\Sdk\emulator\emulator.exe`
- adb: `C:\Users\28928\AppData\Local\Android\Sdk\platform-tools\adb.exe`
- AVD: `Medium_Phone_API_36.1`（x86_64 / API 36 / Google Play）
  - `C:\Users\28928\.android\avd\Medium_Phone_API_36.1.ini` 存在；`D:\android-home\avd` 与 `C:\Users\28928\.android\avd` 目录内容一致（同一份 AVD）。
- APK: `dist/点点高斯-v0.1-x86_64.apk`，105,723,738 B
- 包名 `com.diandi.gaosi`，入口 `.MainActivity`
- 主机可用内存约 3 GB，AVD 配置 2048 MB（不调高）

## 步骤 0 · 前置检查

- `adb devices` 初始：空（daemon 刚启动，无设备）。
- APK 大小核对：105,723,738 B ✓
- 截图目录 `docs/artifacts/shots/emu` 已创建。

## 步骤 1 · 启动模拟器 ✅

- 第一次尝试（`-memory 2048` + `nohup &` 脱离）：emulator 打印 3 行 INFO 后**静默退出**，进程不在 tasklist 中，stderr 为空。原因未明（疑似 Git Bash 下 detach 后随父进程回收）。
- 第二次尝试：改为「后台任务前台运行 + tee 落盘」，并降到 `-memory 1536`（主机内存仅约 3 GB）：**成功**。
  - 关键日志：`Graphics backend: gfxstream`、`AVD supportsNativeGLES=1, supportsGuestAngle=1`、`Guest Angle is still unstable for API > 35.`、`Created extended window in 3.053815s`
  - 进程：`emulator.exe` (PID 41560) + `qemu-system-x86_64.exe` (PID 44028, 约 2.0 GB 常驻)
  - 冷启动耗时：约 06:50:15 启动 → 06:51:22 `sys.boot_completed=1`，**约 67 秒**。
- `adb devices`：`emulator-5554  device`
- 落盘：`docs/artifacts/emu-run2.log`（模拟器完整输出）

## 步骤 2 · 安装 APK ✅

命令：`adb install -r dist/点点高斯-v0.1-x86_64.apk`

```
List of devices attached
emulator-5554	device

--- install ---
Performing Streamed Install
Success
EXIT=0
```

- **没有出现 `INSTALL_FAILED_NO_MATCHING_ABIS`**，ABI 匹配正常（x86_64 包 + x86_64 AVD）。
- 耗时约 2m41s（105 MB 流式安装）。
- 落盘：`docs/artifacts/install.log`
- 踩坑记录：本机每次 bash 调用结束会回收 adb daemon，新 daemon 起来的头几秒设备显示 `offline`。**解决办法：同一条 bash 命令里先 `adb start-server` 并 `sleep 8`，再执行后续 adb 操作。**

## 步骤 3 · 启动应用 + 抓 logcat ✅

- `adb shell monkey -p com.diandi.gaosi 1` → `Events injected: 1`
- 进程存活：`u0_a227 2347 ... R com.diandi.gaosi`
- `dumpsys activity activities`：`topResumedActivity=ActivityRecord{... com.diandi.gaosi/.MainActivity t43}`，**MainActivity 已前台**。
- WebView 正常起来：
  - `WebViewFactory: Loading com.google.android.webview version 134.0.6998.135 (code 699813538)`
  - `Start proc 2500:com.google.android.webview:sandboxed_process0:... for com.diandi.gaosi`
- **应用本身无崩溃、无 ANR。**
- logcat 里唯一的 ANR 是系统应用 `com.google.android.apps.wellbeing`（pid 1955，`failed to complete startup`），发生在 app 启动之前，是模拟器冷启动期间的系统抖动，**与本项目无关**（同一时段还有 `com.android.phone died`、`com.google.android.as` tombstone 等系统噪声）。
- 已知无害告警：`chromium: [ERROR:variations_seed_loader.cc(39)] Seed missing signature.`、`[WARNING:dns_config_service_android.cc(81)] Failed to read DnsConfig.`、`cr_ChildProcessConn: Fallback to ... SandboxedProcessService1` —— 均为 WebView 常规噪声。
- 落盘：`docs/artifacts/logcat-boot.txt`（1642 行）

> 补充（源码核对，只读）：`app/assets/js/app.js:14` 的 `TABS` 只有 **3 个**：`新建 / 图库 / 设置`。
> 「运行」是 `Progress.open()` 全屏进度浮层（`assets/js/progress.js`），「查看」是 `Viewer.open()` 全屏层（`assets/js/viewer.js`，由图库点开项目触发）。
> 所以本次截图覆盖：新建页（≈导入）、图库页（≈图库池）、设置页，以及查看层/进度层（若能进入）。

## 步骤 4 · 截图

（见下方追加）
