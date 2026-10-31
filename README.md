# 点点高斯（Diandi Gaosi）— 完整源码包

安卓端侧（全离线）3DGS 高斯泼溅重建 App。把环绕拍摄的视频/照片，在本机重建成可交互查看、可编辑、可分享的高斯模型。

- **包版本**：v0.1（x86_64 交付）
- **打包日期**：2026-10-20
- **用途**：换机器继续开发 / 备份 / 网盘存档

---

## 0. 先读这一段

这个包是**完整可编译的工程**，不是只有 APK 的空壳。拿另一台电脑后：

1. 解压到**不含中文和空格的路径**（Android/NDK 工具链对中文路径敏感），例如 `D:\src\diandi-gaosi`
2. 用 Cursor / VS Code 打开**解压出来的 `diandi-gaosi` 目录本身**（`build.ps1` 所在的那一层）
3. 只改代码不打包 → 只需 JDK 17 + Android SDK
4. 要重新编译原生引擎 → 另需 Rust + NDK（见 §3.2）

**注意**：如果你手上只有「点点高斯」那个只装了 APK 的文件夹，那是安装目录，不是源码。源码就是本包。

---

## 1. 30 秒上手（只改 UI / 只打包）

```powershell
# Windows PowerShell 7（必须 7.x，5.1 会因 UTF-8 无 BOM 的中文注释误判语法）
cd D:\src\diandi-gaosi
.\build.ps1 -Abi x86_64        # 打模拟器包（默认）
.\build.ps1 -Abi arm64-v8a     # 打真机包
.\build.ps1 -Abi all           # 两个都打
```

产物落在 `dist\`，`BUILD COMPLETE` + `BUILD_EXIT=0` 才算成功。

**UI 改动不需要重新编译 Java**：界面全部在 `app/assets/` 下（HTML/CSS/JS），改完直接重新跑 `build.ps1` 即可（aapt2 会重新收集 assets）。

---

## 2. 目录地图

| 路径 | 体积 | 是什么 | 改它需要 |
|---|---|---|---|
| `app/` | 12 MB | **Android 层**：Java 壳 + WebView + 全部前端资源 | JDK 17 + SDK |
| `app/src/com/diandi/gaosi/` | — | 13 个 Java 类（见 §2.1） | javac |
| `app/assets/js/` | 132 KB | 前端逻辑：`edit.js` / `viewer.js` / `pages.js` / `app.js` 等 | 任意编辑器 |
| `app/assets/runtime/` | 9.6 MB | App 内查看用的 splat-transform + PlayCanvas | — |
| `app/assets/share/runtime.js` | 2 MB | **HTML 分享**用的离线 PlayCanvas 运行时（完整内嵌，零外链） | — |
| `build.ps1` | 5.5 KB | 手工打包脚本（aapt2 → javac → d8 → 注入 → zipalign → apksigner） | PowerShell 7 |
| `tools/` | 9.8 MB | 取证/验证脚本：`imgstats.py`（像素统计判据）等 | Python 3 |
| `docs/` | 7.2 MB | 规格、架构、决策记录、复盘、验证截图 | — |
| `native/sfm/` | 20 MB | **C++ SfM 引擎**源码 → `libiii_sfm.so` | CMake + NDK + OpenCV |
| `native/brush/` | 132 MB | **Rust 训练引擎**（Brush v0.3.0 fork）→ `libbrushheadless.so` | Rust + NDK |
| `native/brush/crates/` | 78 MB | 真正的源码（含自改的 `lpips` crate） | — |
| `native/brush/.git/` | 54 MB | Brush 的上游版本历史，便于追溯 diff | — |
| `native/deps/` | 232 MB | **必需依赖**：burn / cubecl / egui / wgpu 的本地 path 依赖 | — |
| `native/opencv-install/` | 672 MB | OpenCV Android 预编译产物（两套 ABI），SfM 编译依赖 | — |
| `native/prebuilt/` | 131 MB | **已编译好的 `.so`**（两个 ABI），不打原生也能直接出 APK | — |
| `native/testdata/` | 78 MB | 测试素材（视频/数据集） | — |
| `native/*.sh` | — | 模拟器启动、GPU/Vulkan 探测、ABI 测试脚本 | — |
| `dist/` | 70 MB | **已签名的 APK**（中英文名指向同一文件，只保留一份） | — |

### 2.1 Android 层类一览

| 文件 | 职责 |
|---|---|
| `MainActivity.java` | 入口：启动本地 HTTP 服务 → 加载 WebView → 注入 JS 桥；启动时自动跑一次存储清扫 |
| `Bridge.java` | JS ↔ Java 唯一通道（`window.Diandi.*`）。编辑、分享、任务、项目管理接口都在这里 |
| `PipelineManager.java` | 重建流水线编排：抽帧 → SfM → 训练 → 导出，进度/ETA/取消 |
| `PipelineService.java` | 前台服务，保证锁屏/切后台训练不断 |
| `ProjectStore.java` | 图库池：`filesDir/projects/<id>/` + `index.json`。**含 id 白名单与路径断言** |
| `EditStore.java` | **3DGS 编辑数据层**：mask 读写/校验、crop、transform、流式导出 edit.ply |
| `HtmlShare.java` | **单文件 HTML 生成器**：流式 base64 分片 + 离线运行时内嵌 |
| `ShareFileProvider.java` | 分享用的 ContentProvider（只暴露 `cacheDir/shared/`） |
| `HttpServer.java` | 本地 HTTP：`/model/<id>.ply`、`/cover/<id>.jpg`、静态资源 |
| `MediaImport.java` / `FrameExtractor.java` / `AppState.java` / `EngineProbe.java` | 素材导入、抽帧、全局状态、引擎探测 |

---

## 3. 环境依赖

### 3.1 只打 APK（改 UI / Java）

| 依赖 | 版本 | 本机路径示例 |
|---|---|---|
| JDK | **17** | `C:\Program Files\Eclipse Adoptium\jdk-17.0.4.8-hotspot` |
| Android SDK Build-Tools | **36.1.0** | `%LOCALAPPDATA%\Android\Sdk\build-tools\36.1.0` |
| Android Platform | **android-36.1** | `%LOCALAPPDATA%\Android\Sdk\platforms\android-36.1` |
| Python | 3.x（打包辅助脚本用） | — |
| PowerShell | **7.x**（`pwsh`） | 5.1 不支持 |

`build.ps1` 顶部有 5 个路径变量（`$root`/`$sdk`/`$bt`/`$androidJar`/`$javaHome`），**换机器第一件事是改这 5 行**。

### 3.2 重编原生引擎（一般不需要）

| 依赖 | 用途 |
|---|---|
| Rust 1.99+ | 编 `libbrushheadless.so`（`native/brush/build_android.sh`） |
| NDK **27.2.12479018** | 两个引擎都要 |
| CMake 3.22+ | 编 `libiii_sfm.so`（`native/sfm/build_android.sh`） |
| OpenCV Android SDK | 已在 `native/opencv-install/` 备好 |

**除非你要改 Rust/C++ 逻辑，否则不必重编**：`native/prebuilt/` 里已有两个 ABI 的成品 `.so`，`build.ps1` 直接取用。

---

## 4. 三条功能的实现要点

### 4.1 3DGS 编辑

**非破坏性预览 + 破坏性导出**：

- 编辑状态只写一个 `edits/deleted-{revision}.mask`（约 30 KB），`final.ply` **永不改动**
- 渲染时按位跳过被删的 splat
- 导出才重写 PLY：改 `element vertex N`，按序拷贝保留顶点字节

**mask 文件格式**（从桌面版逆向确认，逐字节对拍过）：

| 偏移 | 类型 | 含义 |
|---|---|---|
| 0x00 | char[8] | magic `OOSMASK\0` |
| 0x08 | u32 LE | version = 1 |
| 0x0C | u32 LE | revision（乐观锁版本号） |
| 0x10 | u32 LE | 保留 = 0 |
| 0x14 | u64 LE | sourceSplatCount |
| 0x1C | u64 LE | deletedCount |
| 0x24 | u64 LE | maskBytes = `ceil(N/8)` |
| 0x2C | u64 LE | checksum = **FNV-1a-64(payload)** |
| 0x34 | u8[] | bitmask，**LSB-first**：`bit i = payload[i>>3] >> (i&7) & 1`，**1 = 已删除** |

FNV-1a-64：`h = 0xcbf29ce484222325; for b: h ^= b; h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF`

**裁剪语义 = 保留区域内**（`kind` 0=无 / 1=球`center+radius` / 2=盒`center+size`）。
从桌面版 shader 原文确认：`return loadOoosplatDeleted().r > 0.5 || !ooosplatCropContains(center);` —— 不在区域内即丢弃。

**变换**：`position[3]` / `rotation[3]`（欧拉角，**单位度**）/ `scale`（标量）。
⚠️ 目前 **transform 只落盘元数据，不参与导出**，仅用于即时预览；退出编辑时会归零预览变换，保证「看得见 = 导得出」。若要烘焙进 PLY，顺序必须为 `T·R·S`、`R = Rz(rz)·Ry(ry)·Rx(rx)`、绕原点（与投影拾取同一套矩阵）。

### 4.2 图库池删除 / 自动清理

- **id 白名单** `[A-Za-z0-9_-]{1,64}` + `getCanonicalPath()` 断言是 `projects/` 的直接子目录且不等于根目录。
  双重拦截的原因：`new File(root, "")` 会解析成 `projects/` 根目录本身，早期版本会把**所有作品一次删光**。
- **自动清理** `ProjectStore.sweep()`：失败或僵死（`running` 超 30 分钟且非活跃任务）的项目，删掉 `frames/` `dataset/` `colmap/` `tmp/` 四个中间目录，**保留** `meta.json`、封面和 `final.ply`。App 启动时自动跑一次。
- 前端删除后强制刷新（绕过 600 ms 节流），避免残留幽灵卡片。

### 4.3 单文件 HTML 分享

照搬桌面版 `preview.html` 方案，产物是**完全离线的单 HTML**：

- PlayCanvas 2.21.4（rev `e287e0c`）完整内嵌为 base64（解码后 1,998,982 B），**零外链**
- PLY 数据切成 **每片 1,048,576 个 base64 字符**（4 的倍数，保证 3 字节对齐），片间无分隔符
- bootstrap 3 行：`atob` → `TextDecoder` → 建 `<script>` 注入执行
- 体积公式：`HTML ≈ 2.681 MB + 顶点数 × 314.67`

实现要点：**流式**（每次读 786,432 字节原始 PLY → base64），绝不把整份 PLY 读进内存，否则手机必 OOM。
品牌已改为「点点高斯」，运行时里的他方 logo 元素保留但 `display:none`（runtime 的 `getElementById('logo')` 拿到 null 会抛异常）。

---

## 5. 已知限制（诚实清单）

1. **真机 / 模拟器未跑过完整交互**。工具条在真实 Android WebView 的布局、`pointer*` 手势行为、gsplat 实体应用非单位变换后 PlayCanvas 是否真按世界变换渲染——这三项需上机确认。
2. **分享面板兼容性未实测**：微信/蓝牙/邮件对 `text/html` + `EXTRA_STREAM` 的处理需要装机验证。
3. **涂抹删除不做遮挡判断**：屏幕半径内的 splat 一律命中，会连带删掉物体背面。对策是换角度再刷。
4. **编辑预览非 60fps**：350 ms 防抖，因为 `plyToGlb` + GLB 上传是重活。表现为「停手后稍等一下才变」，不会卡死。
5. **编辑期多占一份 PLY 内存**（`viewer.js` 保留 `curBuf`）。低内存机型若出问题，这里是第一个要动的地方。
6. **APK 体积口径**：x86_64 包 47,036,450 B = **44.86 MiB**（45 MiB 内 ✓），十进制 47.04 MB。
7. **未实现**：账号/云同步、AR 采集引导、transform 烘焙、压感笔刷、连通域选中、多端并发编辑冲突检测。

---

## 6. 未打进本包的内容（可重建）

| 排除项 | 体积 | 为什么 | 怎么重建 |
|---|---|---|---|
| `native/brush/target/` | **13 GB** | Rust 编译缓存 | `cd native/brush && ./build_android.sh x86_64`（首次约 1–2 小时） |
| `native/sfm/build-x86_64/` `build-arm64-v8a/` | 278 MB | CMake 中间产物 | `cd native/sfm && ./build_android.sh <abi>` |
| `build/`（根目录） | 455 MB | APK 打包中间产物 | 跑一次 `build.ps1` 自动生成 |
| `native/hostrun/` `hostrun2/` | 83 MB | 桌面演练产物 | 无需重建 |
| `dist/dian-di-gao-si-*.apk` | 70 MB | 与中文名 APK 是同一文件（硬链接） | 无需重建 |
| `D:/brush-target` | — | 另一份 Rust target（曾用于绕过链接器问题） | 无需重建 |

**关键**：`native/prebuilt/` 已经包含两个 ABI 编译好的 `.so`，所以即使不重建上面任何一项，也能直接打出可安装的 APK。

---

## 7. 验证记录（关键数字）

| 项目 | 实测结果 |
|---|---|
| APK 签名 | v2 + v3 = true |
| zipalign | `-c -P 16 4` PASS |
| 16KB 页对齐 | 两个 `.so` 均为 STORED，数据起始偏移 `%16384 == 0` |
| mask 校验 | 与桌面真实文件对拍，FNV-1a-64 = `9f3bdacfc1609417` / `ae4a3472d46425c3` 命中 |
| 导出顶点数 | `240477 − 118574 = 121903`，输出 28,770,724 B 与桌面 `edit.ply` 等长 |
| 删除精确性 | 22 项断言全 PASS（`delete("")` / `"."` / `".."` / `"../projects"` 全部拒绝） |
| HTML 分享 | 真实 64 MB PLY → 88,549,330 B / 82 片；**分片拼接解码 md5 与原始 PLY 完全相同** |
| HTML 离线性 | 全文件 `https://` 命中 0 次 |

---

## 8. 排障

| 现象 | 原因 / 处理 |
|---|---|
| `build.ps1` 报「意外的标记 }」 | 用了 PowerShell 5.1。改用 `pwsh`（7.x）运行 |
| javac 报大量「GBK 不可映射字符」 | 漏了 `-encoding UTF-8`。`build.ps1` 内已有，手动跑需自己加 |
| `linking with link.exe failed` | Git Bash 的 GNU `link` 顶替了 MSVC 链接器；或环境缺 VS Build Tools。确保用 `build.ps1` 而不是手工 cargo |
| `zipalign` 通过但自己算偏移不为 0 | 算偏移不能读 `zipfile.ZipInfo.extra`，要读 local header 的 `nlen/elen`：偏移 = `header_offset + 30 + nlen + elen` |
| 装机后白屏 | 看 logcat 里 `DIANDI_JS` 与 `Bridge` 标签；也可能是原生引擎未接入（设置页会点名缺哪个） |
| 训练崩在 `vkQueueSubmit` | 模拟器的 gfxstream Vulkan 已知问题，非本工程 bug；真机不受影响 |

---

## 9. 许可与商标

- 本工程前端使用 PlayCanvas Engine（MIT），相关声明保留在 `app/assets/share/runtime.js` 与分享产物内。
- `native/brush/` 来自 Brush（见 `native/brush/LICENSE`），改动遵循其原许可。
- 桌面参考产品 OOOSplat 为**他方商标**，本工程仅作兼容性参考，产物中不出现其品牌。
