# native-sfm：点点高斯 SfM 引擎

负责人：native-sfm
状态：**方案 B 已落地。arm64-v8a 与 x86_64 两个 ABI 的 `libiii_sfm.so` 均已产出；x86_64 已在 Android 模拟器上真跑并产出 COLMAP 三件套（44 帧全量：44/44 注册、7696 个 3D 点、平均重投影 0.38 px；11 帧子集：10/11 注册、2153 个 3D 点、0.45 px）。arm64 只做了编译 + ELF 校验，未在设备上执行——本机只有 x86_64 模拟器，没有 arm64 设备。**

---

## 1. 结论摘要（verdict）

| 项目 | 结果 |
|---|---|
| 采用方案 | 方案 B 变体：OpenCV 4.10 交叉编译静态库 + 自写增量 SfM + 自写 COLMAP 序列化 |
| 最终产物 | `diandi-gaosi/native/prebuilt/arm64-v8a/libiii_sfm.so`、`.../x86_64/libiii_sfm.so` |
| 输出格式 | COLMAP 二进制 `cameras.bin` / `images.bin` / `points3D.bin`，训练器零转换 |
| 是否真 SfM | 是。真实特征提取 + 真实匹配 + 真实位姿估计（R/PnP + RANSAC）+ 真实三角化 + 真实光束法平差 |
| 验证方式 | 真实视频抽帧（44 帧），在 Android x86_64 模拟器（API 36.1）上执行引擎，产出三件套后用独立实现的 Python 读取器交叉校验，两轮均 PASS（详见 §8） |
| 下游串联 | 已跑通：11 帧与 44 帧两组的 COLMAP 产物均被 brush 训练器正确消费并在**真 GPU**（RTX 5060）上训出 PLY，几何已经我复核确认无 scale drift（详见 §9） |
| **唯一缺口** | **安卓 arm64 真机上的完整实跑仍未做过。** §8/§9 的所有性能与显存数字分别来自 x86_64 模拟器（SfM）和 Windows 桌面 GPU（训练），均非安卓真机，不可直接照搬 |

---

## 2. 选型决策与理由

### 2.1 为什么放弃方案 A（COLMAP / GLOMAP）

调研确认 COLMAP 4.x 确实把 GLOMAP 合并为 `global_mapper`，全局 SfM 比增量 mapper 快 10–50x，
理论上对 CPU 友好。但在本机落地有三个硬伤：

1. **依赖链太长**：Ceres（需要 BLAS/LAPACK + miniglog）+ Boost + SQLite + FreeImage。
   本机没有系统包管理器，也没有预装 cmake / ninja / 任何 host C++ 编译器，全部要手工交叉编译。
2. **拿不到预编译包**：GitHub release 附件域名 `objects.githubusercontent.com` 被本机代理拦截
   （`CONNECT tunnel failed, response 502`），`github.com` 本体同样 502。COLMAP / vcpkg 的
   预编译产物、vcpkg 的 arm64-android port（vcpkg#33359 已知失败）都无从获取。
3. **FreeImage 是最大麻烦点**：需要给 JPEG/PNG/TIFF/EXR 等一系列第三方库逐个打补丁，
   用 stb_image 替换又要改 COLMAP 源码多处。

判定：方案 A 在本机是典型的依赖地狱，且**不能保证在剩余时间内拿到可运行产物**。

### 2.2 为什么选方案 B（OpenCV + 自写增量 SfM）

1. OpenCV 4.4 之后 **SIFT 已进入主仓 `features2d`**（专利到期），`cv::SIFT::create()` 直接可用，
   不需要 opencv_contrib / nonfree。
2. `calib3d` 提供经过工业验证的 `findEssentialMat` / `recoverPose` / `findFundamentalMat` /
   `solvePnPRansac` / `triangulatePoints`，这些自己写不仅工作量大且极易出隐蔽 bug。
3. 自写的部分（增量注册、三角化过滤、BA、COLMAP 序列化）都是可以单测、可控、可调参的纯算法代码，
   风险集中在自己手里而不是依赖链里。

### 2.3 两个 ABI 的 OpenCV 来源不同（历史原因，非设计）

- **arm64-v8a**：一开始 `objects.githubusercontent.com` 被拦截，拿不到官方 SDK，
  于是从 `codeload.github.com` 拉 OpenCV 4.10.0 源码 tarball（该域名未被拦截），
  用 NDK + CMake + Ninja 自己交叉编译了 6 个模块的静态库（core / imgproc / features2d /
  calib3d / imgcodecs / flann），装到 `native/opencv-install/arm64-v8a/`。
- **x86_64**：后来用 GitHub 代理 + `curl -C -` 断点续传把官方
  `opencv-4.10.0-android-sdk.zip`（302,037,755 字节，1326 个文件）拉下来了，
  直接复用其中的 `sdk/native/staticlibs/x86_64` 与 `sdk/native/jni/include`，
  **没有再编译**。版本号与 arm64 一致（4.10.0）。

两者 ABI 与版本都对齐，混用没有风险。代价是 x86_64 产物明显更大：官方包启用 IPP
（x86 专属优化，`libippicv.a` 99 MB），静态链接会带进来一部分 IPP 代码。

> 源码 tarball 下载曾中途被截断（拿到 75,315,752 字节，gzip 报 unexpected EOF），但 tar 内的
> 目录是按字典序打包的，`3rdparty/` 与 `modules/` 均完整（校验过 libjpeg-turbo 115 文件、
> libpng 31 文件、imgproc/src 102 文件、calib3d/src 50 文件齐全），只有尾部 `samples/data`
> 缺失，不影响编译。

### 2.4 方案 C（OpenMVG）未被采用

OpenMVG 不导出 COLMAP 格式，需要自写转换器，成本比方案 B 高，且同样要交叉编译其依赖
（Eigen + cereal + lemon 等），没有优势。

---

## 3. 构建步骤

### 3.1 前置

```bash
python -m pip install cmake ninja imageio-ffmpeg numpy   # 本机没有 cmake/ninja
```

- NDK：`C:/Users/28928/AppData/Local/Android/Sdk/ndk/27.2.12479018`
- OpenCV 源码：`_deps/opencv-4.10.0`（tarball 解压）
- Eigen 3.4：已 vendor 到 `native/sfm/third_party/eigen`（header-only）

### 3.2 OpenCV 静态库

- **x86_64（推荐路径，不需要编译）**：下载官方 `opencv-4.10.0-android-sdk.zip`，
  把 `sdk/native/staticlibs/x86_64`、`sdk/native/3rdparty/libs/x86_64`、
  `sdk/native/jni/include` 按 arm64 那份的布局放到 `native/opencv-install/x86_64/` 即可。
  本机 `github.com` 被代理 502，实际走的是 GitHub 代理 + `curl -C -` 断点续传
  （脚本：`_deps/fetch_sdk.sh`）。
- **arm64-v8a（源码编译路径）**：

```bash
cd diandi-gaosi/native/sfm
bash build_opencv.sh arm64-v8a
```

关键 CMake 参数（省略全部无用依赖）：

```
-DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake
-DANDROID_ABI=<abi> -DANDROID_PLATFORM=android-24 -DANDROID_STL=c++_static
-DCMAKE_BUILD_TYPE=Release
-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY   # 关键：去掉几百次 link 测试
-DBUILD_LIST=core,imgproc,features2d,calib3d,imgcodecs,flann
-DBUILD_SHARED_LIBS=OFF -DENABLE_PIC=ON
-DBUILD_ANDROID_PROJECTS=OFF -DBUILD_ANDROID_EXAMPLES=OFF -DINSTALL_ANDROID_EXAMPLES=OFF
-DBUILD_JAVA=OFF -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF -DBUILD_DOCS=OFF
-DWITH_IPP=OFF -DWITH_OPENCL=OFF -DWITH_CUDA=OFF -DWITH_PROTOBUF=OFF
-DWITH_ADE=OFF -DWITH_QUIRC=OFF -DWITH_FFMPEG=OFF -DWITH_GTK=OFF -DWITH_EIGEN=OFF
```

### 3.3 编译引擎

```bash
bash build_android.sh arm64-v8a
bash build_android.sh x86_64
```

产物落到 `diandi-gaosi/native/prebuilt/<abi>/libiii_sfm.so`，自动 `llvm-strip --strip-all`。

---

## 4. 遇到的问题与解法

| # | 问题 | 解法 |
|---|---|---|
| 1 | `github.com` / `objects.githubusercontent.com` 被代理拦截（502） | 改用 `codeload.github.com` 拉源码 tarball；Eigen 从 `gitlab.com` 拉 |
| 2 | 本机无 cmake / ninja / 任何 C++ 编译器 | `pip install cmake ninja`，编译器用 NDK 自带的 clang++ |
| 3 | 头文件 `features.h` 与 libc++ 内部的 `<features.h>` 冲突（`-I src` 优先级高，导致 `__config` 把我的头文件当系统头引入，报几百个 `_LIBCPP_*` 未定义） | 改名为 `feature_extract.h` |
| 4 | `opencv2/opencv_modules.hpp` 是生成文件，源码树里没有，直接语法检查会失败 | 语法检查时在临时目录补一份最小 `opencv_modules.hpp` |
| 5 | OpenCV configure 报 `Unknown CMake command "add_android_project"` | 必须加 `-DBUILD_ANDROID_EXAMPLES=OFF -DINSTALL_ANDROID_EXAMPLES=OFF`（`BUILD_ANDROID_PROJECTS=OFF` 不够） |
| 6 | OpenCV configure 在本机跑了 30+ 分钟还没完 | 加 `-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY` 去掉所有 link 测试 |
| 7 | 安全中心拦截 3 个 `.o.d` 依赖文件的写入，`libopencv_calib3d.a` 链接失败 | 用 NDK clang++ 手工编译 `calibration.cpp` / `ptsetreg.cpp` / `compat_ptsetreg.cpp`（去掉 `-MD -MT -MF`），补上 `.o` 后 ninja 续跑成功 |
| 8 | 链接报 `undefined symbol: gzopen / gzputs / gzeof ...` | OpenCV 用的是系统 zlib，链接参数补 `-lz` |
| 9 | 本机 adb 极不稳定（daemon 每次都被重建，设备反复 offline；Git-Bash 还会把 `/data/...` 转成 Windows 路径） | 所有 adb 调用放进同一条 shell 命令，前置 `adb start-server; sleep 3; adb wait-for-device`，并设 `MSYS_NO_PATHCONV=1` |
| 10 | 长时后台任务会被回收 | 把 `cmake --build` 拆成前台多次调用，ninja 天然可续跑 |
| 11 | 官方 SDK zip（302 MB）拿不到：GitHub release 附件被 502 拦截，SourceForge 也连不上 | 用 `gh.llkk.cc` / `gh-proxy.com` 等 GitHub 代理，配合 `curl -C -` 断点续传、多镜像轮换（脚本 `_deps/fetch_sdk.sh`，约 30 分钟下完） |
| 12 | adb daemon 在**每次 bash 调用结束后就被回收**，下一次调用时 `adb devices` 直接空 | 整条验证流程必须写在一次 bash 调用里，先 `adb start-server`，再轮询最多 40×3 s 等设备上线；脚本 `tools/run_device.sh` |
| 13 | 开了 `MSYS_NO_PATHCONV=1` 之后 `adb push` 报 `cannot stat '/c/...'` | adb.exe 是 Windows 程序，本地路径必须写成 `C:/...`；远程 `/data/...` 才需要 `MSYS_NO_PATHCONV=1` |
| 14 | 设备上跑到 matching 阶段直接 abort：`libc++abi: terminating due to uncaught exception of type cv::Exception: OpenCV(4.10.0) .../core/src/batch_distance.cpp:303: error: (-215:Assertion failed) K == 1 && update == 0 && mask.empty() in function 'batchDistance'` | `cv::BFMatcher` 的 `crossCheck=true` 与 `knnMatch(K=2)` **互斥**（`batchDistance` 的 crosscheck 分支有 `CV_Assert(K == 1)`）。改成 `crossCheck=false` 做 knnMatch(K=2) + Lowe 比值，互相最近邻自己反向再查一次（`src/matching.cpp:rawMatch`） |
| 15 | 自检脚本一开始误报 12399 个"悬空 point3D 引用" | 是我自己的 Python 读取器写错了：COLMAP 的 2D 点是**交错**存放的（每点 `double x, double y, int64 point3D_id` 共 24 字节），我却按"先读全部 xy 再读全部 id"解析。修正后 dangling = 0 |
| 16 | **images.bin 多写了 `uint64 name_len`，brush 一读就 panic**：`thread 'main' panicked at library/alloc/src/raw_vec/mod.rs:28:5: capacity overflow`，EXIT=101 | COLMAP 二进制 images.bin 的图像名是 **NUL 结尾、不带长度前缀**的（官方 `read_write_model.py` 逐字节读到 `\0`；brush 用 `read_until(b'\0')`）。去掉长度前缀，并且我自己的读取器也改成同样读法。另外补了防御：`num_points2D` 超过 2000 万就报错，避免坏数据带着分配巨量内存。**这个 bug 是我自己的自检脚本没抓到的——因为它和写入端犯了同一个错误，属于"自证"；是 brush 这个独立实现把它暴露出来的。** |

---

## 5. 二进制契约

调用：

```
exec <nativeLibDir>/libiii_sfm.so --images <dir> --out <colmapDir> \
     [--max-size 1600] [--overlap 5] [--features 2048] \
     [--exhaustive] [--txt] [--verbose]
```

**注意**：文件名叫 `.so`，但它是 **PIE 可执行文件**（`llvm-readelf` 显示 `Type: DYN`），
用 `exec` 调用而不是 `dlopen`。已在 Android 上实测 `exec` + argv 透传正常。
若目标设备上 `/data/app/.../lib` 目录不可执行，UI 侧应把它从 assets 拷到
`context.getFilesDir()` 再 `chmod 755` 后执行。

进度（stdout，每行一个 JSON，行缓冲）：

```
{"stage":"features","current":12,"total":44,"registered":0,"points":0}
{"stage":"matching","current":30,"total":120,"registered":0,"points":0}
{"stage":"mapping","current":17,"total":44,"registered":17,"points":5231}
{"stage":"done", ...}
{"stage":"error","message":"..."}
```

退出码：`0` 成功；`2` 参数错误；`3` 读不到图片；`4` 有效特征不足两张；`5/6` 匹配失败；
`7` 重建失败；`8` 写文件失败。

---

## 6. 序列化格式（字段级对齐 COLMAP）

全部小端。严格对齐官方 `scripts/python/read_write_model.py`：

```
cameras.bin
  uint64 num_cameras
  per camera: int32 camera_id, int32 model_id, uint64 width, uint64 height, double params[n]
      model_id = 2 (SIMPLE_RADIAL), params = (f, cx, cy, k1)

images.bin
  uint64 num_reg_images
  per image:  int32 image_id
              double qvec[4]      # (qw, qx, qy, qz)
              double tvec[3]
              int32  camera_id
              char name[]         # NUL 结尾, **没有长度前缀**
              uint64 num_points2D
              per point2D: double x, double y, int64 point3D_id   (-1 = 无)

points3D.bin
  uint64 num_points3D
  per point:  int64  point3D_id
              double xyz[3]
              uint8  rgb[3]
              double error
              uint64 track_len
              per track elem: int32 image_id, int32 point2D_idx
```

> **踩过的坑（务必别改回长度前缀）**：第一版在 `camera_id` 之后多写了一个 `uint64 name_len`。
> 这会让下游把 8 字节长度字段当成名字的一部分，再把文件名的前几个字节当成 `num_points2D`，
> 于是 `num_points2D` 变成 ~8e18，`Vec::with_capacity()` 直接 `capacity overflow` panic
> （brush 在设备上就是这个表现，见 §4 问题 16）。
> 官方 `scripts/python/read_write_model.py::read_images_binary` 是**逐字节读到 `\0` 为止**，
> brush 的 `colmap-reader` 用 `read_until(b'\0')`，两者都不读长度前缀。

**分辨率约定**：重建过程在处理分辨率（长边 ≤ `--max-size`）下进行；写出时
`f / cx / cy` 与所有 `points2D` 坐标都乘以 `scale` 还原到原始分辨率，
`cameras.bin` 里的 `width/height` 写**原始尺寸**。
这样 COLMAP 目录与磁盘上的原图严格自洽，训练器直接按 `width/height` 用即可。

写出后引擎会**回读自检**：重新解析三个 bin，校验 camera model id、焦距为正、
四元数模长为 1，并在 `done` 行里回报 `verified_bin` 与读回的计数。

---

## 7. 算法细节

1. **特征提取**：OpenCV SIFT，每帧 ≤ 2048 特征，`contrastThreshold=0.02`。
2. **匹配**：序列窗口（第 i 帧 ↔ i+1..i+overlap，外加 i+2*overlap..i+4*overlap 的回环对），
   复杂度 O(N·overlap) 而非 O(N²)；BFMatcher + Lowe 比值 0.8 + 互相最近邻；
   基础矩阵 F 的 RANSAC（阈值 1.5 px）几何校验，内点 < 20 的对直接丢弃。
3. **Track 构建**：并查集，把跨帧匹配点归并成 track。
4. **初始像对**：按几何内点数降序取前 12 对；每对做
   本质矩阵 RANSAC + `recoverPose` + 三角化 + 前方交会符号检查 + 中位视差角评估；
   评分 `good × min(angle, 30)`，要求中位视差角 ≥ 8°，否则放宽重选。
5. **增量注册**：每次选「2D-3D 对应数最多」的未注册帧；PnP RANSAC（EPnP + 内点迭代精化），
   要求内点 ≥ 18 且内点比例 ≥ 0.15；注册后再用自写 LM 精化该帧位姿。
6. **三角化**：DLT（最多 12 视角）+ 前方交会 + 重投影 < 3 px + 最小视差角 1.5°。
7. **光束法平差**：自写 LM，Schur 消元（点 3×3 块对角求逆，相机侧 `6N+4C` 稠密 Cholesky），
   Huber 损失（δ = 5 px，按 2 维残差范数加权），固定第一帧消除 6 自由度规范自由度，
   同时优化共享内参 `(f, cx, cy, k1)`。80 帧时相机侧仅 484×484。
8. **几何计算的坐标系**：所有两视角/PnP 计算都在**去畸变后的归一化相机坐标**下做
   （牛顿迭代反解 `r(1 + k1 r²) = r_d`），避免内参与畸变耦合造成偏差。

---

## 8. 实测数据

### 8.1 产物

| ABI | 路径 | 体积（strip 后, bytes） | ELF 校验（llvm-readelf -h） |
|---|---|---|---|
| arm64-v8a | `diandi-gaosi/native/prebuilt/arm64-v8a/libiii_sfm.so` | 7,051,920 | `Class: ELF64` / `Type: DYN (Shared object file)` / `Machine: AArch64` |
| x86_64 | `diandi-gaosi/native/prebuilt/x86_64/libiii_sfm.so` | 24,809,256 | `Class: ELF64` / `Type: DYN (Shared object file)` / `Machine: Advanced Micro Devices X86-64` |

x86_64 明显更大，是因为它链接的是官方预编译 SDK（启用 x86 专属的 IPP 优化，
`libippicv.a` 99 MB），静态链接会带进一部分 IPP 代码；arm64 是自编译的最小 6 模块版本。
两者都是 PIE（`Type: DYN`），可以用 `exec` 直接跑。

（本机没有 `file` 命令，架构校验用的是 NDK 的 `llvm-readelf`。）

### 8.2 精度 / 耗时

**验证环境**：Android x86_64 模拟器（sdk_gphone64_x86_64，API 36.1），CPU 单进程执行。
**输入**：`ooosplat_input/54734.mp4`（640×1136，8.70 s，29.99 fps）用 ffmpeg
`select='not(mod(n\,6))'` 抽出的 44 帧；下面 Run A 取其 11 帧等间隔子集。
**命令**：`./libiii_sfm.so --images <frames> --out <colmap> --max-size 1024 --overlap 8`

#### Run A（11 帧，完整闭环，已拿到三件套）

| 指标 | 数值 |
|---|---|
| 输入图片数 | 11 |
| 成功注册图片数 | **10**（90.9%，有 1 帧未通过 PnP 内点门槛） |
| 重建 3D 点数 | **2153** |
| 观测数（track 元素） | 6338 |
| 平均重投影误差（points3D 内存储值） | **0.4496 px**（p90 0.8958 px） |
| 引擎自报平均重投影误差 | 0.479 px |
| 初始像对视差角 | 16.50° |
| 解出的内参 | f = 1362.967 px，cx = 320.555，cy = 568.458，k1 = 0.0396（图像 640×1136） |
| 相机轨迹步长 | min 0.3554 / mean 0.9715 / max 1.6923（世界单位） |
| 相机轨迹包围盒跨度 | 2.6754 |
| track 长度 | min 2 / mean 2.94 / max 9 |
| 点云包围盒 | x[-0.44, 1.73] y[-1.22, 0.52] z[2.70, 3.98] |
| **总耗时** | **57.82 s**（elapsed_ms = 57820） |
| 写盘产物 | cameras.bin 64 B、images.bin 447,406 B、points3D.bin 160,515 B |

**交叉校验**：`tools/inspect_colmap.py`（另写的一份纯 Python COLMAP 读取器，不复用引擎代码）
解析三个 bin，结果 `dangling point3D refs = 0`、`VERDICT: PASS`。
唯一的小差异：从 images.bin 数到的有效观测是 6333，从 points3D track 数是 6338
（差 5 条，是少数 2D 点在后续 filter 阶段被改挂到别的 point3D 所致），COLMAP 自身也允许这种情况。

#### Run B（44 帧全量，完整闭环）

命令：`--max-size 800 --overlap 5`（第一次用 `--max-size 1024 --overlap 8` 跑到第 33 帧 /
7815 点时 adb 连接被重置，没拿到产物；改成设备端 `nohup` 后台执行 + 本机轮询日志后跑完）。

| 指标 | 数值 |
|---|---|
| 输入图片数 | 44 |
| **成功注册图片数** | **44 / 44（100%）** |
| **重建 3D 点数** | **7696** |
| **总耗时** | **686.5 s**（elapsed_ms = 686549，约 11.4 min；模拟器 CPU） |
| 观测数 | 35771（images.bin 与 points3D.bin 两侧计数**完全一致**） |
| 平均重投影误差 | 0.3822 px（p90 0.7553 px） |
| 初始像对视差角 | 18.06° |
| 解出内参 | f = 1362.298 px，cx = 320.711，cy = 568.341，k1 = 0.1147 |
| 相机轨迹步长 | min 0.1097 / mean 0.2995 / max 0.6042 |
| 相机轨迹包围盒跨度 | 4.7014 |
| track 长度 | min 2 / mean 4.65 / max 35 |
| 点云包围盒 | x[-2.00, 3.49] y[-7.07, 3.02] z[1.80, 23.35] |
| 写盘产物 | cameras.bin 64 B、images.bin 1,485,300 B、points3D.bin 678,672 B |

`tools/inspect_colmap.py` 交叉校验：**dangling refs = 0，VERDICT: PASS**。

#### 产物存档

两份验证产物已归档在仓库里，可以直接复核：

- `native/testdata/verify_x86_64_11/`（Run A 三件套）—— **修复 images.bin 名字布局之后重新跑出来的**
- `native/testdata/verify_x86_64_44/`（Run B 三件套）—— 由修复前的那次运行产出，
  之后只做了**纯字节级重排**：去掉每条记录里多写的 8 字节 `name_len`，几何内容一字未动
  （`images.bin` 1,485,300 → 1,484,948 B，正好少 44×8）。已用 COLMAP 官方读取器复核：
  44 images / 7696 points / 61713 个 2D 点 / 名字全部正常。

给 brush 用的数据集目录也一起备好了（布局 `images/` + `sparse/0/`）：

- `native/testdata/ds_11/`、`native/testdata/ds_44/`
- `native/testdata/ds_11_noinit/`（同上但去掉 `points3D.bin`，用来绕开 §9.3 的 headless bug）

复现命令：

```bash
python diandi-gaosi/native/sfm/tools/inspect_colmap.py diandi-gaosi/native/testdata/verify_x86_64_44
bash  diandi-gaosi/native/sfm/tools/run_brush.sh diandi-gaosi/native/testdata/ds_11 30 128
```

> 说明：模拟器 CPU 比真机慢很多（VIVO X300 Pro 是骁龙 8 Elite），57.8 s / 11 帧这一数字
> 应视为**上界**；真机上同样规模预计快数倍。

### 8.3 验证边界（不夸大）

- **x86_64**：在 Android 模拟器上真实 `exec` 跑通，三件套落地，数值如上。
- **arm64-v8a**：只做到「编译 + ELF 头校验 + 与 x86_64 同一份源码同一套参数」。
  **没有 arm64 设备或 arm64 模拟器可用，未在 arm64 上执行过**。本机唯一可用的设备是
  x86_64 模拟器（`adb shell getprop ro.product.cpu.abi` → `x86_64`）。

### 8.4 与 COLMAP 官方读取器的格式一致性

除自写的 `tools/inspect_colmap.py` 之外，还用**第三方独立实现**做过一次字段级校验：
COLMAP 3.8 官方 `scripts/python/read_write_model.py`（`read_cameras_binary` /
`read_images_binary` / `read_points3D_binary`）能无错解析我们的三件套，且语义正确：

```
cameras 1 [(1, 'SIMPLE_RADIAL', 640, 1136, [1362.967, 320.555, 568.458, 0.04])]
images  10
names first3 ['frame_0001.jpg', 'frame_0005.jpg', 'frame_0009.jpg'] last frame_0041.jpg
sample img id=1 cam=1 npts=2049 q=[ 0.9967 -0.0805 -0.0119 -0.0042] t=[-0.0703 -0.2781 -0.1974]
points3D 2153
sample pt id=1 xyz=[ 0.767 -0.92  3.402] rgb=[121 135 157] err=0.2913 track=7
```

### 8.5 两个 ABI 的产物一致性（给真机首跑排雷）

arm64 那份没法在本机 arm64 设备上执行（环境限制），所以退一步把**代码路径一致性**钉死，
这样真机上一旦出问题可以先排除「两个 ABI 走了不同分支」：

| 检查项 | arm64-v8a | x86_64 | 是否一致 |
|---|---|---|---|
| 参与编译的源文件 | 同 6 个 `src/*.cpp` | 同 6 个 `src/*.cpp` | ✅ 同一份源码树，`build.ninja` 里路径完全相同 |
| OpenCV 头文件版本 | 4.10.0 | 4.10.0 | ✅ 两个 `opencv-install/<abi>/sdk/native/jni/include/opencv2/core/version.hpp` 都是 `4 / 10 / 0` |
| 编译 flags（`matching.cpp.o`） | `-O3 -DNDEBUG -std=gnu++17 -fPIE -O3 -ffast-math -fomit-frame-pointer -march=armv8-a` | 同上，**没有** `-march=armv8-a` | ✅ 唯一差异就是那条 arm 专属 `-march` |
| 架构条件编译 | — | — | ✅ `src/` 里 grep `__aarch64__` / `__x86_64__` / `__ARM_NEON` / `__SSE__` / `ANDROID_ABI` **零命中**，没有任何 per-ABI 分支 |
| 两个修复是否都进了产物 | `matching.cpp.o` 16:35:03、`colmap_io.cpp.o` 18:01:40 | `matching.cpp.o` 16:28:47、`colmap_io.cpp.o` 17:59:08 | ✅ 都晚于对应源码的修改时间（16:27:40 / 17:57:45），两个 ABI 都包含了 BFMatcher 修复和 images.bin 名字布局修复 |
| 动态依赖 | `libz libdl libm liblog libc` | `libz libdl libm liblog libc` | ✅ 完全一致 |
| PIE | `FLAGS_1: NOW PIE` | `FLAGS_1: NOW PIE` | ✅ |

**残留的唯一不一致**：arm64 链的是我自己交叉编译的 OpenCV（`-DWITH_IPP=OFF` 等），
x86_64 链的是官方预编译 SDK（**开了 IPP**，x86 专属优化）。版本号相同，但优化后端不同，
理论上可能带来浮点层面的极小差异。这是给真机首跑留的一个已知变量。

---

## 9. 引擎级串联验证（SfM 产物 → brush 训练器）

把 SfM 的三件套按 COLMAP 目录约定组装成数据集（`images/` + `sparse/0/`），
用 `native/prebuilt/x86_64/libbrushheadless.so` 在**同一台 x86_64 模拟器**上跑，
区分三件事：① brush 能否正确消费 COLMAP ② 训练能否跑完 ③ 崩在那一层。

脚本：`native/sfm/tools/run_brush.sh`（adb push + 设备端后台执行 + 轮询 + pull ply）。

### 9.1 ① brush 能正确消费我们的 COLMAP —— 成立

`RUST_LOG=info` 日志逐条证明（11 帧数据集，10 张注册图）：

```
Loading colmap dataset
Loading colmap dataset with 10 images
Loaded COLMAP image at path "images/frame_0001.jpg"      (×10, 10/10 全中)
Starting from colmap points 2153                          <- 用到了我们的 points3D.bin
Created init splat from points
Loaded 10 training views
```

- 10 张图**全部按 images.bin 里的 name 字段找到了**，没有一张 "Image not found"。
- `cameras.bin` 被解析成 SIMPLE_RADIAL（brush 的 `colmap-reader` 支持 model_id=2）。
- `points3D.bin` 的 2153 个点被当成初始 splat 用（"Starting from colmap points 2153"）。

即：字段名、坐标系（w2c → brush 自己求逆成 c2w）、quaternion 顺序、稀疏点云都对得上。

> 注：这个结论是修掉 §4 问题 16 之后才成立的。修复前 brush 一读 images.bin 就
> `capacity overflow` panic（EXIT=101），根本走不到"加载图片"这一步。

### 9.2 ② 在**模拟器**上训练跑不完 —— 崩在 brush/驱动层，与 SfM 无关

去掉 `points3D.bin` 绕开下面 9.3 的 headless bug 后，训练走到
`Start training loop.` 就 `Segmentation fault`，**EXIT=139**，没有产出 PLY。

**这是环境/驱动问题，不是数据问题**，证据：

- **对照组**：用仓库自带的 `native/testdata/tiny_colmap`（6 张图、官方 .txt 格式、
  与 SfM 完全无关）在同一台设备同一个 binary 上跑，**也崩在同一个位置、同样的 EXIT=139**。
- **tombstone**（`/data/tombstones/tombstone_23`）显示崩溃点是模拟器自己的 Vulkan 驱动，
  不在 brush 代码里：

```
signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0000000000000000 (read)
Cause: null pointer dereference
  #00 pc 00008e715  /vendor/lib64/hw/vulkan.ranchu.so  std::__1::vector<unsigned long>::push_back+165
  #01 pc 00009fa2d  /vendor/lib64/hw/vulkan.ranchu.so  gfxstream::vk::ResourceTracker::on_vkQueueSubmit+4509
  #02 pc 00010a3be  /vendor/lib64/hw/vulkan.ranchu.so  gfxstream_vk_QueueSubmit+2158
  #03 pc 000023c85  /system/lib64/libvulkan.so         vulkan::driver::QueueSubmit+53
  #04..            /data/local/tmp/brush_test/libbrushheadless.so
```

- **不是内存**：崩溃前后 `MemFree` 1.14–1.52 GB、`MemAvailable` 2.2–2.4 GB，
  `ulimit -s` = 8192。所以"模拟器内存不够"这个假设在这台设备上**不成立**。
  （本机这台模拟器 `MemTotal` = 3,041,872 kB，不是 AVD config 里写的 2048。）

推论：换真机（有正常 Vulkan 驱动）大概率能跑完；模拟器上要让训练跑通，
得让 wgpu 走 CPU 后端或换 swiftshader/guest 渲染，这属于 native-brush / APK 侧的范畴。

### 9.3 ③ 顺带发现的 brush-headless bug（属于 native-brush）

数据集**带 `points3D.bin`** 时，`brush-process` 的 `train_stream.rs:81` 会 emit
`ProcessMessage::ViewSplats`；而 `brush-headless/src/main.rs:247` 把 `ViewSplats`
当成致命错误直接 bail：

```
Error: <path> contains only ply files; a COLMAP dataset directory is required
EXIT=1
```

即 **headless 模式下只要数据集提供了初始点云就必然失败**（与我们的数据无关，
任何带 points3D 的 COLMAP 数据集都会触发）。对比：`brush-cli` 对 `ViewSplats`
是忽略（`lib.rs:152` 注释 "I guess we're already showing a warning"），
`brush-ui` 也是正常处理。建议 headless 同样忽略该消息。

**已经修好了**（native-brush，2026-10-14）：`brush-headless/src/main.rs:247` 改成忽略 +
记一条 `log::info!("Dataset provides initial splats, using them")`，与 `brush-cli` 一致。
修复之后 `std_dataset_11` 在真 GPU 上完整跑通，见 9.4。

---

### 9.4 真 GPU 上的完整结果（全链路跑通）

native-brush 修掉 9.3 的 bug 之后，在 **Windows 原生 target + RTX 5060 真 NVIDIA Vulkan 驱动**
（没有 gfxstream）上用 `std_dataset_11` 跑通了，产物留档在
`native/testdata/brush_real_splat.ply`（555,686 B，`element vertex 6033`，SH degree 1）。

| | 30 步 smoke | 2000 步正式跑 |
|---|---|---|
| 首 iter splats | 2153（= 我们的 points3D 点数） | 2153 |
| 末 iter splats | 2605 | **6033** |
| 首 loss | 0.4152（iter 5） | 0.4258（iter 5） |
| 末 loss | 0.4037 | **-0.1676** |
| 耗时 | 2.17 s | **24.0 s** |
| 退出码 | 0 | **0** |

判据五行全部出现（含修复后新加的 "Dataset provides initial splats, using them"），
一条不差，没有 `Image not found`。

#### 几何一致性核对（我这边做的复核）

把 `brush_real_splat.ply` 的 6033 个 splat 中心与 `std_dataset_11` 的 2153 个 SfM 点做最近邻比对：

```
splat 质心      : ( 0.587, -0.556,  3.338)
SfM 点云质心    : ( 0.645, -0.350,  3.340)      <- 几乎重合，坐标系与尺子完全一致

到最近 SfM 点的距离:  p50 0.0165   p75 0.0570   p90 0.1951   p95 0.3930   p99 1.3733
距离 < 0.2 的 splat  : 90.2%
距离 < 0.5 的 splat  : 96.3%
落在 SfM bbox 内的   : 5503 / 6033 = 91.2%
```

**结论：坐标系、朝向、尺度全部正确**——90% 的 splat 到最近 SfM 点的距离小于 0.2
（场景本身尺度约 3，即 6% 以内），质心几乎重合。

需要说明的一点：splat 的**整体 bbox**（`x[-9.06,9.49] y[-6.16,4.61] z[0.47,7.24]`，
对角线 22.49）比 SfM 点云 bbox（对角线 3.06）大 7.3 倍。这不是坐标系问题，
而是约 **1.5% 的远场 floater**（最远的一个距离 SfM 点云 9.16）把包围盒撑开了——
3DGS 在只有 10 个视角、无 mask、步数有限的输入上的典型表现。
**判断尺度请以质心和最近邻距离为准，不要只看 bbox。**
  这一条如实标注，不含糊。

---

### 9.5 44 帧大规模基线（ds_44，交 native-brush 在真 GPU 跑）

11 帧那组已经闭环，但它规模太小，证明不了**点数和显存随规模增长**的行为。
44 帧那组（`native/testdata/ds_44/`）已整理成 brush-ready 形态并交给 native-brush
在真 GPU 上补跑，作为更大规模的真机基线。

**数据集（我现在重新复核的结果）：**

```
ds_44/images/             44 个文件（frame_0001.jpg ~ frame_0044.jpg）
ds_44/sparse/0/cameras.bin      64 B
ds_44/sparse/0/images.bin  1,484,948 B
ds_44/sparse/0/points3D.bin 678,672 B

文件名匹配：images.bin 里的 44 个名字 vs images/ 里的 44 个文件 -> 缺失 0
```

`tools/inspect_colmap.py ../testdata/ds_44/sparse/0` 输出：

```
cameras : 1
   id=1 SIMPLE_RADIAL 640x1136 params=1362.298, 320.711, 568.341, 0.115
images  : 44 registered
points  : 7696
observations (from images) : 35771
observations (from tracks) : 35771
dangling point3D refs      : 0
camera center step dist    : min 0.1097  max 0.6042  mean 0.2995
camera center span (bbox)  : 4.7014
mean reproj error (stored) : 0.3822 px
p90  reproj error (stored) : 0.7553 px
track length               : min 2 max 35 mean 4.65
point bbox                 : x[-2.00,3.49] y[-7.07,3.02] z[1.80,23.35]
VERDICT: PASS
```

#### 9.5.1 ds_44 的 images.bin 字节来源问题 —— 已用离线往返序列化证明等价（不需设备）

`ds_44/sparse/0/images.bin` 不是修复版 writer 端到端跑出来的原始字节，而是对那次 44 帧运行
的产物做了**纯字节层重排版**：按每条记录删掉多写的 8 字节 `uint64 name_len`
（1,485,300 → 1,484,948 B，恰好少 44×8），几何数值一个字节都没动。
原因见 8.2 末尾：那轮之后模拟器严重退化，44 帧重跑从 11 分钟掉到 1 小时只走到 features 9/44。

这个"重排版"标签**不需要重跑设备就能摘掉**，纯离线可证：
把三个 .bin 按正确格式完整解析成结构化记录，再严格按 `src/colmap_io.cpp::writeColmapBinary`
的写出顺序重新序列化一遍，与磁盘上的字节逐字节比对。
f64/i64 一律原样搬运（`struct '<d'`/`'<q'`），不涉及浮点格式化，所以往返是**位精确**的。

工具：`native/sfm/tools/roundtrip_colmap.py`

```
python tools/roundtrip_colmap.py ../testdata/ds_44/sparse/0 --legacy-size 1485300

解析结果: cameras=1 images=44 points3D=7696

== cameras.bin ==   磁盘 64 B       往返 64 B       逐字节一致: YES
                    sha256 双方 = be372aa8c127c396aea0e6dfa34cee0e9b0baf925085eae21a361fccaec2a674
== images.bin ==    磁盘 1,484,948 B 往返 1,484,948 B 逐字节一致: YES
                    sha256 双方 = e09ba55347c9678fe307c5ca9b7e93dd99f26fa367ae6afd05e32c92fd7fd489
== points3D.bin ==  磁盘 678,672 B  往返 678,672 B   逐字节一致: YES
                    sha256 双方 = e4e0ae477c2a6cdaa0fc8f0275440422c83ac69f6c4ef0dcf55ef48bbea99671

== 长度账目反证（按修复前布局重算）==
  按错误布局重写: 1,485,300 B
  按正确布局重写: 1,484,948 B
  差值: 352 = 44 x 8
  与记录的修复前字节数 1,485,300 一致 -> 当初确实只删了 name_len，没误伤别的数据

VERDICT: PASS
```

同一工具对 `std_dataset_11`（修复后端到端跑的那份）也是 PASS，三件套 sha256 双向一致
（images.bin 447,326 B / points3D.bin 160,515 B / cameras.bin 64 B）。

**为什么这份证据比重跑设备更硬：** 重跑还可能因为环境差异（OpenCV 后端、线程数、
浮点归约顺序）产生别的字节；而往返比对证明的是"磁盘上的字节就是 writer 对这组 scene
记录的唯一可能输出"，且可随时由第三方复核（`python tools/roundtrip_colmap.py <dir>`）。

**还剩的一个前提要说清**（不掩盖）：以上证明的是**写出层**等价。要保证那次 44 帧运行的
**scene 内容**也与今天的最终产物一致，需要当时跑的二进制在除 writer 外的所有编译单元上相同。
这一点由构建产物时间戳证实：

```
build-x86_64/CMakeFiles/iii_sfm.dir/src/
  ba.cpp.o               16:20:08     matching.cpp.o         16:28:47   <- BFMatcher 修复已包含
  feature_extract.cpp.o  16:19:00     main.cpp.o             16:19:06
  incremental.cpp.o      16:19:49
  colmap_io.cpp.o        17:59:08     <- 唯一被重建的（name_len 修复, 源改动 17:57:45）

产出 verify_x86_64_44 的那次运行        ~17:41
prebuilt/x86_64/libiii_sfm.so 链接时间  17:59:31
```

即 17:59 那次重链接只重编了 `colmap_io.cpp.o`，其余五个 .o 全部复用；
而 44 帧那次运行发生在 17:41，用的是同一批 .o。所以两者在 SfM 计算路径上**是同一份目标代码**，
差异只有 writer。**结论：ds_44 的三件套等价于今天的最终 libiii_sfm.so 在同一组输入上的输出。**

（arm64 那份 `.o` 的 matching 时间戳是 16:35，晚于 x86_64 但早于 44 帧运行；
arm64 从未在 arm64 设备上执行过这一点不变，见 8.3 的验证边界。）

#### 9.5.2 上游数据的已知瑕疵：44 帧点云带 0.7% 远场离群点（量化表）

这是交给下游前必须说明的**上游已知瑕疵**，否则容易被当成新 bug 查错方向。

```
                  11 帧 (std_dataset_11)        44 帧 (ds_44)
点数                  2153                          7696
点质心          ( 0.583, -0.684, 3.377)      (-0.126, -0.474, 4.288)
到质心 p50/p90       0.600 / 0.863               0.944 / 1.500
到质心 p95/p99       1.216 / 1.404               1.666 / 2.032
到质心 max           1.51                        19.12
半径 > 3 的点        0    (0.0%)                 52   (0.7%)

相机光心质心          -                     (0.182, 0.120, -0.346)，共 44 个
光心相邻步长          -                     min 0.110 / max 0.604 / mean 0.300
每帧 2D 点数          -                     min 130 / max 2048 / mean 1402.6
```

**来源：44 帧里视差偏弱的那些三角化 lane**——11 帧那组完全干净（max 1.51，半径 > 3 的点为 0），
规模上去之后才出现这 52 个点。属于 SfM 侧的真实瑕疵，不是序列化问题
（9.5.1 已证明写出字节与 writer 完全等价）。
11 帧那组则是干净的（max 1.51，半径 > 3 的点为 0）。这些离群点来自 44 帧里
视差偏弱的那些三角化 lane，是 SfM 侧的真实瑕疵。
含义：若这次训出的 PLY 出现比 9.4 更明显的 floater / bbox 被撑大，
**先怀疑是这 52 个点，不要先怀疑 brush**；判定尺度仍以「splat 质心 vs SfM 质心」
和「最近邻距离」为准，不要只看 bbox。

#### 9.5.3 真 GPU 实测结果（native-brush 跑，RTX 5060 + 真 NVIDIA Vulkan）

**① 字节重排版等价性 —— 判据全部命中**

```
Loading colmap dataset with 44 images
Loaded COLMAP image at path ...  = 44 条
Image not found                  = 0 条
Starting from colmap points 7696        <- 与 points3D.bin 的点数完全一致
Dataset provides initial splats, using them
Loaded 44 training views
```

44 条图像名全部解析到、无缺失、初始 splat 数 = 7696，与 9.5.1 的离线证明互相印证。

**② smoke（30 步）与完整基线（2000 步）**

| | 30 步 smoke | 2000 步基线 |
|---|---|---|
| 参数 | res512 / sh1 / max-splats 200000 / refine10 / growth25 / export10 / seed42 | res1024 / sh1 / max-splats 2000000 / refine100 / growth1800 / export500 / seed42 |
| 退出码 | 0 | 0 |
| 耗时 | 8,834 ms | **68,421 ms** |
| splats | 7696 → 9306 | 7696 → **21680** |
| loss | iter5 0.27864 → iter30 0.41567 | iter5 0.27872 → iter2000 **-0.15254** |
| PLY | 856,802 B | **1,995,211 B**，`element vertex 21680`，`comment SH degree: 1` |

留档 `native/testdata/brush_real_splat_44.ply`。

**③ 规模横向对比（同机器、同参数、真 Vulkan）**

| | 11 帧 | 44 帧 |
|---|---|---|
| 初始点 | 2153 | 7696 |
| 末 splats | 6033 | 21680 |
| 首 loss | 0.42581472 | 0.27872235 |
| 末 loss | -0.16757166 | -0.15253961 |
| 耗时 | 24.015 s | 68.421 s |

帧数 4×、初始点 3.6×，耗时 2.85× —— **sub-linear**，符合预期。

**④ 显存峰值**（`nvidia-smi -l 1` 全程 88 个样本）

```
空闲基线 1584 MiB（含该机器上其它占 GPU 的进程）
训练峰值 2488 MiB
训练净增 ~904 MiB   /   总显存 8151 MiB
```

口径必须带上"含其它进程基线"，否则这个数字不可复现。

**平台声明：以上全部是 Windows 真 GPU（RTX 5060）的数字，不是安卓真机。**
手机是**统一内存架构**，0.9 GB 的净增**不能**直接照搬过去推断手机可行性。
安卓 arm64 的完整实跑目前仍是唯一缺口，等 apk-shell 插上真机。

**⑤ 几何一致性复核（我这边用 tools/check_ply.py 独立算的）**

`python tools/check_ply.py ../testdata/brush_real_splat_44.ply ../testdata/ds_44/sparse/0`

```
质心: splat (-0.0943, -0.4373, 4.3080)  vs  SfM (-0.1261, -0.4739, 4.2885)   偏差 0.0522

每个 splat 到最近 SfM 点的距离:
  p50 0.0150  p75 0.0342  p90 0.1095  p95 0.2348  p99 0.7306  max 16.0300
  < 0.1 : 89.3%       < 0.2 : 94.1%       < 0.5 : 98.0%       < 1.0 : 99.4%

半径（以 SfM 质心为中心，两侧同一口径）:
  splat p50 0.9982  p90 1.7194  p95 1.9384  p99 4.1892  max 20.8350
  SfM   p50 0.9440  p90 1.5002  p95 1.6659  p99 2.0318  max 19.1185
  半径>3: splat 396 (1.83%)   SfM 52 (0.68%)
  半径>5: splat  82 (0.38%)   SfM 15 (0.19%)

bbox: splat 对角线 41.728   SfM 对角线 24.425   落在 SfM bbox 内 99.4%
```

对照 11 帧那组（同一工具同口径）：质心偏差 0.1347，< 0.2 的 splat 90.2%，落在 SfM bbox 内 91.3%。

**结论：坐标系、朝向、尺度全部正确，没有 scale drift。** 44 帧质心偏差只有 0.05（场景尺度约 4，即 ~1.3%），
98% 的 splat 到最近 SfM 点的距离小于 0.5。

**重要限定：本小节 ⑤⑥ 以及 §9.5.4 / §9.5.5 里所有"训练后"的数字，
都是 `res1024 / sh1 / 2000 步 / seed42` 这一个参数的快照，不是训练侧的一般规律。**
native-brush 后来在同一份数据集上把训练拉到 **6000 步**（其余参数不变，见 9.5.6），
结果直接推翻了下面两条 2000 步的结论，引用时必须带上"2000 步"这个前提。

关于那 52 个离群点是否被放大 —— **2000 步下"没失控"，但这是快照，不是定论**：
- max 半径 19.12 → 20.84，只涨 9%。若 brush 在生成失控 floater，涨幅会大得多。
  **（已被 9.5.6 推翻：6000 步下 max 半径 20.84 → 26.51，涨 27%；11 帧那组 10.35 → 19.93，涨 92%。）**
- brush 反而**剪掉**了最远的 z（SfM 最远 z 23.35 → 训练后 17.21），极端远场点被裁而非被放大。
  **（已被 9.5.6 推翻：6000 步下 z 上限回到 23.31，基本等于 SfM 的 23.35。这是 2000 步的巧合。）**
- 但 radius>3 从 0.7% 涨到 1.8%：点数 7696→21680 是 2.8× 加密，按比例应约 146 个，实际 396 个，**高于线性**，
  说明在原本视差就弱的 lane 上确实多长了点。
- 最近邻距离 max 16.03 说明仍有零星远场 floater，**判尺度请以质心和最近邻为准，不要看 bbox**
  （splat bbox 对角线 41.7 是 SfM 的 1.7 倍，但这不代表尺度错）。

**锅的归属已被改写 —— 见 9.5.5 与 9.5.6：远场尾部的"存在"是训练侧系统现象，
我那 52 个点只抬高"数量"，既不是它的成因也不是必要条件。**

**⑥ 远场增长倍率：随距离单调下降 —— "没有失控"的定量判据**

只看"0.68% → 1.83%"容易看不出方向，把它拆成分距离带的增长倍率
（整体加密倍率 7696 → 21680 = **2.82×**）：

```
累计 (>r)      SfM    训练后    倍率
  > 3           52      396     7.62x
  > 5           15       82     5.47x
  >10            4       11     2.75x

距离带         SfM    训练后    倍率     若按整体 2.82x 等比应得
  (3,5]        37      314     8.49x     104
  (5,10]       11       71     6.45x      31
  (10,100]      4       11     2.75x      11

结构比 (>5 占 >3 的比例):  SfM 28.8%  ->  训练后 20.7%
```

判读：**倍率随距离单调下降**（7.62 → 5.47 → 2.75）。若是失控 floater，
应当看到相反趋势——越远涨得越凶。而且最远那一带 (10,100] 的增长倍率 **2.75×
几乎等于整体加密倍率 2.82×**，说明最远那层外壳只是跟着整体等比变密，没有额外外扩。
多出来的点集中在中间那条壳 (3,5]，不是往外推。
另有结构比佐证：>5 占 >3 的比例从 28.8% 降到 20.7%，**尾部相对变薄**。

更稳的一张表是半径分位倍率（不受分箱样本量影响）：

```
半径分位     SfM      训练后     倍率
  p50       0.9440    0.9982    1.06x
  p75       1.1595    1.2832    1.11x
  p90       1.5002    1.7194    1.15x
  p95       1.6659    1.9384    1.16x
  p99       2.0318    4.1892    2.06x     <- 唯一明显增长的一档
  max      19.1185   20.8350    1.09x
```

在点云整体加密 2.82 倍的前提下，**p95 壳只长大 1.16 倍、最远端只长大 1.09 倍** ——
点云整体没有膨胀。唯一需要如实指出的是 **p99 的 2.06 倍**，对应上面 (5,10] 那档的 6.45 倍，
即确实有一小撮点被推到了 p99 这一圈上，但它们没有继续往外跑（max 未变）。

**小样本声明（不夸大）：** 远端绝对计数很小（SfM 侧 >10 只有 4 个点、5–10 只有 11 个点），
`2.75×` 这个数字本身经不起单独引用。真正有说服力的是
**跨距离带的单调下降趋势 + 上面那张分位倍率表**两条互相独立的证据，而不是某一个倍率点。

**另一个容易看错的数字：不要用"落在 SfM bbox 内"当尺度判据。**
44 帧是 21555/21680 = **99.4%**，看着比 11 帧的 91.3% 漂亮得多，但**这个数字更弱**，
因为参照物本身不一样：

```
                  11 帧            44 帧
落在 SfM bbox 内    91.3%            99.4%
SfM bbox 对角线      3.068           24.425     <- 后者被那 52 个离群点自己撑大了 8 倍
```

拿一个被离群点撑大的盒子去框，自然几乎全中。**要一句话判定尺度，用上面的分位倍率表，
不要用 bbox 包含率。**

---

### 9.5.4 位置尾 vs 尺度尾：两个尾部要分开归因

native-brush 提出尾部应该分成**位置**和**尺度**两个维度，这个切分本身是对的，值得保留；
但他给的尺度侧数字**有单位错误，我复核后修正了**。过程与结论都记在这里，因为这个坑很典型。

**存储口径先说清**（两条证据）：
- `opacity` 的取值范围是 [-5.0, 7.1]，出现大量负数 → 存的是 **logit**（sigmoid 之前的值）
- `scale_0/1/2` 同理存的是 **log(scale)**，真实世界尺度要 **`exp()`** 之后才是世界单位
  （exp 后 p50 = 0.0318~0.0407，约场景尺度 4 的 1%，量级正常；若当成线性值则 p50 是负的，明显不对）

**修正点：** native-brush 那张阈值计数表把**未经 exp 的 log 值直接当成世界单位**去比阈值了。
我用 `max(raw)` 复算，每格都与他报的表完全一致（证明这就是他的口径）：

```
阈值             >0.6  >0.8  >1.0  >1.2  >1.5
11帧 max(raw)     73    57    39    30    15   <- 逐格复现他的数字
44帧 max(raw)     68    50    39    29    17

而他的另一句表述 —— "p50 只有 0.032–0.041，占场景尺度约 1%" —— 是**做过 exp 的**（正确）。
同一个分析里两个口径混用了，这是修正的依据。
```

**修正后的正确数字（exp 之后按世界单位计数）：**

```
阈值(世界单位)   >0.6  >0.8  >1.0  >1.2  >1.5
11帧 (6033)      229   165   132   111    91
44帧 (21680)     424   278   199   139    96
倍率            1.85  1.68  1.51  1.25  1.05    （整体加密 3.59×）
```

**结论：结构上有东西，但不是他说的那个形式。**

| 尾部 | 11 帧 | 44 帧 | 倍率 | 归因（**已被 9.5.5 改写**） |
|---|---|---|---|---|
| 位置 radius > 3 | 39 | 396 | **10.2×** | 见 9.5.5：**成因不在 SfM 侧**，我那 52 个点只放大数量（10.2×），不决定存在与否 |
| 尺度 > 1.0 | 132 | 199 | 1.51× | 随输入增长，但明显低于整体加密 3.59× |
| 尺度 > 1.5 | 91 | 96 | **1.05×** | 绝对数量基本恒定，**这部分确实与输入规模无关** |

也就是说：**"恒定"这个词要慎用** —— 倍率 1.85 → 1.68 → 1.51 → 1.25 → 1.05 是**单调渐变**，
不是"要么恒定要么随输入长"的二值，也没有一条明确的分界线。
准确表述：**尾部增长始终慢于模型整体（3.59×），且越往极端越慢，到 exp>1.5 时接近等比（1.05×）。**

（**这一句是我自己后来又推翻的**：见下面"交叉拆分"—— `exp>1.5` 那个 1.05×
其实是两个反向子群**互相抵消**的结果，不能说那儿存在一个恒定群体。）

**交叉拆分：把"远"和"大"交叉之后，三个群体的行为完全不同**
（本机核验，native-brush 提出交叉分群并要求限定我的措辞）

```
群体                    11 帧    44 帧    倍率      判读
远但小 (r>3, exp<=1.0)     3      283    94.3x     输入离群点被加密 -> 归因在这里
远且大 (r>3, exp>1.0)     36      113     3.14x    随模型等比（模型自身 3.59x）
近但大 (r<=3, exp>1.0)     96       86     0.90x    绝对数量基本不变 -> 训练侧内在成分
```

由此我**撤回**上一版里"存在一个约 90~100 个的恒定极端大 splat 群体"这句话。
native-brush 的限定是对的，而且比他要求的更严重：把 `exp>1.5` 那一层再按远近拆开看，

```
                     11 帧   44 帧    倍率
近且极大 (r<=3, >1.5)   60      29    0.48x    下降
远且极大 (r>3,  >1.5)   31      67    2.16x    上升
--------------------------------------------
极大合计 (>1.5)         91      96    1.05x    两个反向子群抵消后的巧合
```

**"合计恒定"是抵消出来的假象，不是有一个稳定群体。** 真要说哪个群体在绝对数量上不变，
那是 **近但大（96 → 86，0.90×）** —— 但这批是紧贴主体的大 splat，根本不是 floater，
把它叫"逃逸点"是我上一版措辞的错误。

**最能一眼区分来源的特征**（native-brush 给，我复算 92.3% / 28.5% 一致）：

```
远点中同时是大 splat 的比例:  11 帧 36/39 = 92.3%
                             44 帧 113/396 = 28.5%
```
干净输入下远场点几乎全是大的（92.3%）；带离群点输入下只有 28.5% ——
因为多出来的那 283 个是 SfM 离群点被加密的产物，**远而不大**。
这两批东西确实不是一回事，但也不是简单的"位置 vs 尺度"二分。

**改写后的归因（三句话，注意都是 2000 步快照）：**
1. 训练侧会**系统性地**产生"远且大"的 tail —— 干净输入（0 个远场点）照样长出 36 个（见 9.5.5）；
2. 这部分随模型规模**等比**增长（3.14× ≈ 3.59×），不是恒定的；
3. **我的 52 个离群点贡献集中在"远但小"那一群**（3 → 283）—— 远而不大，是输入离群点被加密的产物。

> **第 3 句已被 9.5.6 推翻（6000 步对照）**：11 帧那组 SfM 远场点**恒为 0**，
> 6000 步下照样长出 **393 个"远但小"**。所以 SfM 离群点**不是"远但小"的必要条件**，
> 训练侧自己会持续造。正确的表述是：离群点**抬高计数**，不是成因。
> **对上游的含义**：不必为了消除远场尾部去动三角化阈值 —— 那治不了。

**两个尾部确实大面积交叠**（我上一版指出的仍然成立）：
44 帧里 199 个大 splat 有 113 个同时 radius>3（56.8%），396 个远点里 113 个同时 exp>1.0（28.5%）。

**样本量声明：** 以上是**两个数据集、两组同参数训练**的对比，只有 2 个数据点。
"恒定"这个判断是假设不是定论，要坐实需要第三个规模（比如 88 帧）的同参数跑。
在拿到第三个点之前，文档里不下"确认"的结论。

**给下游的正确消费口径**（native-brush 这条建议我认为成立，与上面那组修正后的数字无关）：
- PLY 里的 `scale_0/1/2` 要 **`exp()`** 之后才是世界单位，`opacity` 要过 **sigmoid**
- 包围盒要把 splat 半径算进去，但**必须用位置分位数（p99/p99.5）而不是 min/max** ——
  含半径后 bbox 对角线会被放大：11 帧 22.49 → 114.42（**5.09×**），44 帧 41.73 → 87.32（**2.09×**），
  **放大倍数在两份数据间差 2.4 倍**，所以任何**依赖 min/max 或含 bbox** 的取景系数都必然在某类数据上崩

> **适用范围澄清**：上面这组和 `scale_*` 有关的坑（要 exp、含半径后 bbox 膨胀倍数不稳定），
> **对 apk-shell 现有的 `plyFraming()` 不成立** —— 他只读 x/y/z 位置（按 header 顺序累加 stride
> 定位），算质心 + 分轴 p95，**全程不读 `scale_*`、不 exp、也不用 min/max**。
> 所以他那版不受我和 native-brush 后来这一连串尺度分析结果的影响（包括我那个单位错误的牵连）；
> §9.5.3 里给他测的 3.65× / 4.12× 与入画率 97.4–98.4%，是独立成立的。
> 上面这条口径只对日后**确实要把 splat 半径算进去**的实现有效。

---

### 9.5.5 归因改写：远场尾部是训练侧系统现象，SfM 离群点只加量不扩边界

这一节推翻的是**我自己之前写下的结论**，而且证据一直在我自己的工具输出里，我没看出来。
native-brush 用 11 帧那组做对照时指了出来。**这是我的漏判，如实记录。**

**决定性对照：11 帧的输入是干净的（radius>3 一个都没有），训练后照样长出 39 个。**

```
                     SfM 输入                    训练后
              n     radius>3   radius max      n     radius>3   radius max
11 帧       2153      0        1.5142        6033      39       10.3496
44 帧       7696     52       19.1185       21680     396       20.8350
```

这组数据我在 §9.5.2 里早就打印出来过（"SfM max 1.5142 / 训练后 max 10.3496 /
半径>3: splat 39 · SfM 0"），**但当时我把它读成了"11 帧干净、44 帧才有瑕疵"，
没有往下推一步：** 输入侧一个远场点都没有，输出侧却有 39 个 —— 那这 39 个只能来自训练过程本身。

**改写后的结论：**

- **远场尾部的"存在"是训练侧的系统现象**，与输入是否含离群点无关。
  干净输入照样产生约 39 个 radius>3 的 splat（6000 步下是 **586** 个，见 9.5.6 —— 更硬的证据）。
- **SfM 离群点的作用是"加量"，远大于"扩边界"**：radius>3 的计数从 39 变成 396（约 10×），
  而最远半径只从 19.1185 走到 20.8350（+1.7165，约 **+9%**）。
  （措辞订正：不是"包络完全没动"，而是**计数放大 10 倍、包络只外拓 9%** —— 量级差两个数量级，
   但不能写成"完全没有外拓"。）
- 于是 §9.5.2 那句"traces 有、失控没有、**锅主要在 SfM 侧**"的**后半句作废**。
  前半句成立，但"存在"这件事不能算到我那 52 个点头上。

**两个原始观察（只有 2 个数据点，属观察不是定论，也不写成公式）：**

```
干净输入  (SfM max 1.5142)    ->  训练后 max 10.3496    训练自身推到约 10 个世界单位
带离群点  (SfM max 19.1185)   ->  训练后 max 20.8350    输入更远时，输出仍再外扩约 9%
```

即：**训练过程自身会把 floater 推到约 10 个世界单位的量级**；输入若本就比这更远，
输出仍会再往外扩一点（本例 +9%），不是"就不再扩了"。

**这里一度写过一个公式 —— 已删，原因记录如下：**
我原来把上面两点拟合为 `输出最远 ≈ max(输入最远, ~10)`。native-brush 指出两点：
① 第二组根本对不上（`max(19.12, 10) = 19.12`，实测 20.8350，**差 9%**），
而我在草稿里把 max 的取值写成了 20.84，**连算术都是错的**；
② **2 个数据点配 1 个自由参数，任何含参公式都拟合得上** —— 他随手给的二次型
`sqrt(输入² + R²)` 也能压到 4% 以内。也就是说**拟合度不携带任何信息**。
所以这里不写成公式，只列原始数字 + "2 点观察"标注。想坐实需要第三个规模。

**顺带修正一个 metric 陷阱（两边都别再用倍数当头条）：**
native-brush 最初用"max 半径增长倍数"做对照（11 帧 **6.84×** vs 44 帧 **1.09×**），
我当时的反驳写错了——我说"绝对增量其实是相反的"，**这是错的**：

```
              max 半径            倍数      绝对增量
11 帧   1.5142 -> 10.3496       6.84x     +8.8354
44 帧  19.1185 -> 20.8350       1.09x     +1.7165
```

**倍数和绝对增量方向一致**（6.84× > 1.09×，且 +8.835 > +1.717，都指向 11 帧增长更多），
不存在反转。我那句"相反"是在用一个错误去纠正一个不严谨，比原结论更误导 —— 已撤。

正确的说法是：**两个口径都依赖起点**（起点 1.51 和 19.12 差 12.6 倍），
在这个基础上比增长率或比增量，**跨数据集都没有意义**，谁也救不了谁。
`0 → 39` 之所以无偏，不是因为它和别的口径结论不同，
而是因为**基线恰好为零** —— 从 0 长出 39 个，不依赖任何基数、增长率或阈值口径。

**对我自己的教训：** 我在 §9.5.2 里已经把"11 帧干净"标成上游瑕疵的反面对照，
却没有让它去证伪我自己的归因 —— 现成数据在手边，结论由着先入为主走了。
后面凡是"这一组是干净的/有问题的"这类对照，都要拿去反向跑一遍，不能只当背景板。

---

### 9.5.6 第三组数据点：拉长训练到 6000 步，又推翻两条（其中一条是我写在用的）

native-brush 没有去动 SfM（模拟器已退化到跑不动新一轮），改成**在同一份数据集上只拉长训练**
（2000 步 → 6000 步，`--growth-stop-iter` 同步 1800→5400，其余参数不变）。
这个设计比补第三个数据集更干净：**数据集和训练长度两个变量被分开了**。同机同 GPU，四份产物 EXIT=0。

产物在 `native/testdata/steptest/`。我用 `check_ply.py` 同口径（半径以 SfM 点云质心为原点）独立复算，**逐格吻合**：

```
              splats   SfM远场点   r>3    远且大   远但小   max半径   z上限
11帧/2000      6,033       0        39      36       3    10.3496   7.2407
11帧/6000     64,676       0       586     193     393    19.9275  15.2099
44帧/2000     21,680      52       396     113     283    20.8350  17.2063
44帧/6000    137,136      52     6,027     713   5,314    26.5065  23.3093
```

**被推翻的两条（都在我文档里）：**

**① "远但小 = SfM 离群点继承" —— 假的。**
11 帧那组 SfM 远场点**恒为 0**，6000 步照样长出 **393 个"远但小"**。
SfM 离群点的作用是**抬高一个基数**，不是必要条件。**这条我在 §9.5.4 第 3 句里正写着，现予推翻。**

对上游的含义（也是我之前判断错的方向）：**不必为了消除远场尾部去动三角化阈值 —— 治不了。**
§9.5.2 那句"44 帧远场来自弱视差 lane、11 帧那组完全干净"作为**观测**仍然准确，
但由它推出的"修掉 SfM 离群点就能解决尾部"不成立。

**② "远且大 ∝ 模型规模" —— 只在 44 帧上巧合成立。**
```
11帧: 远且大 5.36x  vs 模型 10.72x   占比 0.597% -> 0.298%（减半）
44帧: 远且大 6.31x  vs 模型  6.33x   占比 0.521% -> 0.520%（持平）
```
两点看着像规律（我当时就是拿 44 帧那组写的"3.14× ≈ 3.59×"），第三点就破。
可靠的那半句只有：**远场增长快于模型整体**（r>3 在两组都是约 15×，而模型只涨 10.72× / 6.33×）。

**③ 顺带推翻 §9.5.3 里我自己列的两条"没失控"证据（都是 2000 步快照）：**
- `max 半径只涨 9%` → 6000 步下 44 帧涨 27%（20.84→26.51），11 帧涨 **92%**（10.35→19.93）
- `brush 剪掉了最远的 z（23.35 → 17.21）` → 6000 步下 z 上限 **23.31**，基本等于 SfM 的 23.35

**唯一没被推翻的**是那条 `0 → 39`（6000 步是 `0 → 586`）—— 因为**基线恒为 0，不受起点影响**。
这印证了上轮得出的判据选择原则：**基线为零的计数才是无偏证据，倍数和绝对增量都依赖起点。**

**口径警告：上面那张表里的"远场"用的是绝对阈值 `半径 > 3`，这个阈值不是尺度无关的。**
（apk-shell 指出，我复核确认。）同一个 r=3 在四组数据里相当于 p95 的
**2.02× / 1.88× / 1.55× / 1.17×** —— 也就是说它在不同数据里抓的不是同一批点，
**跨数据集比"远场占比"是不可比的**。

- **受影响的**：所有"占比"的横向比较（例如 11 帧 0.91% vs 44 帧 4.39%）。
- **不受影响的**：`0 → 39`（`0 → 586`）这类**同一数据集内部**输入 vs 输出的比较 ——
  两边用同一把尺子。换成尺度无关的说法更硬：干净输入那组，**输出最远 / 输入最远 =
  6.83×（2000 步）/ 13.16×（6000 步）**。
- 尺度无关的替代量（本机实测，四组一致单调，但**仍是启发式不是定义**）：

```
              p95/p90   p99/p95   r>1.5*p90   r>2*p90   r>3*p90
11帧/2000      1.083     1.534      1.31%      0.70%     0.36%
11帧/6000      1.096     1.780      1.61%      0.96%     0.53%
44帧/2000      1.127     2.161      2.47%      1.50%     0.36%
44帧/6000      1.337     2.623      4.48%      3.32%     1.22%
```

`p95/p90` 只依赖两个已算的分位数、不需要额外阈值、尺度无关，是目前最稳的诊断量；
但**阈值定在哪没有依据**（4 个点校准不了），归一化占比里的 k 同样是自由参数。
**稳定的是排序（四组单调），不是百分比数值。**

**引用纪律（native-brush 自己提的，我照办）：**
四组里最漂亮的数字是"两个数据集的 r>3 都约 15×"（15.03× / 15.22×）。
**这个 15× 不要当规律引用** —— 它比模型倍率还整齐，恰恰是"点数太少才有的漂亮"，
和当初 `sqrt(a²+R²)` 那类拟合是同一个毛病。能放心用的只有方向性结论：**远场增长快于模型整体**。
后面凡是"这一组是干净的/有问题的"这类对照，都要拿去反向跑一遍，不能只当背景板。

### 9.5.7 交接给 apk-shell 的未决项：真机 PLY 回来后要算什么

取景取分位（`viewer.js` 的 `plyFraming()`）这条链路最后缺的是**真机校准**。apk-shell 已把
`framingQuantile()`（快档 p95 / 精档 p90）落代码并打了诊断日志；**没有**写死"占比超阈值自动降档"，
理由是首跑是快档、占比在线内，加了不触发还要重新打包。我同意这个顺序：先测，再定。

真机 `final.ply` 回来后，我要五个数：**档位、p90、p95、`p95/p90`、`dist_p95/dist_p90`**。判据与优先级：

1. **`p95/p90`** —— 尺度无关，只依赖两个已算的分位数，是目前最稳的诊断量。
   四组实测 1.083 / 1.096 / 1.127 / **1.337**，干净组 ≤1.13，接近污染那组 1.34。
   **阈值只有 4 个数据点校准，是启发式不是定义**，真机数据进来前不写死进代码。
2. **`dist_p95/dist_p90`（阈值无关设计）** —— 连 k 都不需要：两个数都是取景时已经算出来的分位数，
   直接比。若比值明显大于约 1.2，则改用 `dist_p90` 取景。好处是**不引入绝对尺度、不引入外部阈值**；
   坏处是那个 1.2 同样只是 4 点启发式。当前**只作报告口径，未改代码**。
3. **不要用 `r>3` 这类绝对阈值在真机上判占比** —— 单目 SfM 尺度任意，`r>3` 在四组里相当于
   p95 的 2.02× / 1.88× / 1.55× / 1.17×，抓的不是同一批点，跨场景不可比。

还有一个自由参数必须提前说清：**"远场"的定义本身（阈值 k）是自由参数**。
`远场占比 > 1-q 则 q 分位失效`（apk-shell.md §4.8.3）这条是**从分位数定义推出来的、严格成立**，
但它的输入 `s` 依赖 k。所以在 k 选定之前，"当前占比有没有过 5%"**没有答案**，
能确定的只有**排序**（四组单调）。别把这条规则当可以直接读数的仪表用。

### 9.5.8 视锥投影：把"框外飞点到底看不看得见"做实（并撤回我一条推论）

native-brush 用"取景框内"否掉了我的"降步数"推论，同时自己标了一条边界：
**框外飞点不等于看不见，落在相机与主体之间视锥内的会挡住主体**，并点名要我这边
用位姿做投影计数。这个我能做，而且**不需要设备** —— 取景相机不是 SfM 相机，
是 `viewer.js` 用 PLY 自己合成的，把它的构造式照抄一遍即可。

**重建的取景相机**（`viewer.js`：home `yaw=30 / pitch=18`，`fitDistance()` `fill=0.9`，
PlayCanvas 默认 `fov=45` 且是**竖直**方向，`tX = tan(fov/2)·asp`）：

`dist = max(rH/(0.9·tX), rV/(0.9·tY))`，相机位置 `C = 质心 + dist·方向`，看向质心。
**asp 会同时改 dist 和视锥形状**，真机是竖屏，所以 0.45（竖屏）和 1.0（方屏）两个极端都算，
只有两边一致才敢下结论。工具：`native/sfm/tools/occlusion_stats.py`。

先说复现：**他那张圆柱框表我独立复算，5 组里 3 组逐位吻合**（11帧/2000 `35 (0.580%)`、
44帧/2000 `27 (0.125%)`、11帧/6000 `44 (0.068%)`），另两组差 11 个和 7 个
（44帧/6000 我 118 vs 他 107、44帧/13000 我 50 vs 他 57）—— 原因是**分位数采样口径**：
`plyFraming()` 只等间隔采 ≤20000 个点算分位，他的脚本用全量。边界点翻转，**不影响任何结论**。

竖屏 asp=0.45 的投影结果（`r>3` 沿用我们的老阈值，只作组内对照）：

| 组 | splats | 圆柱框内 r>3 | 视锥内 | **视锥内 r>3** | 其中不透明>0.5 | **主体前（潜在遮挡）** | 占 splat | 前且不透明 | 绕 yaw 一圈曾挡前 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 11帧/2000 | 6,033 | 0 | 5,941 | 6 | 0 | 9 | 0.15% | 0 | 95 (1.57%) |
| 44帧/2000 | 21,680 | 0 | 21,352 | 103 | 8 | 113 | 0.52% | 8 | 472 (2.18%) |
| 11帧/6000 | 64,676 | 0 | 63,571 | 36 | 0 | 113 | 0.17% | 0 | 1,151 (1.78%) |
| 44帧/6000 | 137,136 | 0 | 133,703 | **2,638** | 41 | 279 | 0.20% | 4 | 1,474 (1.08%) |
| 44帧/13000 | 300,326 | 0 | 291,269 | **4,253** | 61 | 726 | 0.24% | 2 | 4,007 (1.33%) |

方屏 asp=1.0 下同一趋势但量级小一个数量级（主体前 3 / 12 / 15 / 11 / 61），
因为 dist 从 12.5 缩到 5.6、视锥也跟着收窄。**结论在两个极端下一致。**

**三条读数：**

1. **他那条自我限定是对的，而且量不小。** 圆柱是"水平 ≤h95 且竖直 ≤v95"，
   而竖屏视锥在主体深度处的竖直半高是 `tY·dist ≈ 5.18`，远大于 `v95 ≈ 1.68`。
   所以**远场点确实进了画面**：44帧/6000 有 2,638 个、44帧/13000 有 4,253 个 `r>3` 在视锥内。
   "框内=0"是圆柱口径的结论，不能读成"画面里没有远场点"。
2. **但"进画面"≠"挡主体"。** 真正位于主体之前（`depth < dist − r3_p95`）的，
   五组是 9 / 113 / 113 / 279 / 726，占 splat 数 **0.15% / 0.52% / 0.17% / 0.20% / 0.24%**；
   其中不透明度 >0.5 的只有 **0 / 8 / 0 / 4 / 2** 个。剩下的视锥内远场点几乎都和主体同深度、
   偏在画面边缘（"主体后"只有 1–5 个），表现为边缘雾而不是遮挡 —— 观感影响我没量化。
   **（下面"雾团指数"这一段推翻了"计数小 = 看不见"这个读法，请以那段为准。）**
3. **步数拉长没有让遮挡比例变坏：44 帧 2000→6000→13000 是 0.52% → 0.20% → 0.24%。**

**所以我撤回上一轮的推论**：*"如果默认步数偏大，降步数比调取景更治本"*
—— **数据不支持**。全局远场计数（39 → 12,771，327×）确实涨得吓人，
但落到"用户会看到的遮挡"上，比例稳定在 0.2% 上下、不透明的个位数。
他的判断（加长训练不脏画面）在投影口径下**成立**，我的推论是拿全局计数推观感，属于口径错配。
保留的那半句仍然有效：**远场增长快于模型整体**是计数事实；
**但不能用它去推"该降步数"**。

**我这两条也有边界，写清楚：**
- "绕 yaw 一圈曾挡在主体前"是**最坏情况**（用户能拖动旋转）：1.57% / 2.18% / 1.78% / 1.08% / 1.33%，
  **无单调趋势**，绝对数随模型规模涨。只放 yaw 不放 pitch，因为 pitch 全自由会退化成
  "任何远点都可能挡"，那种判据没有信息量。
- 不透明度阈值 0.5 是我取的；3DGS 是累加渲染，0.3 的片也会留痕。真要看清得渲染出图，
  我们手上没有渲染能力，所以这条**只能给计数不能给观感**。

**⚠ 一句话务必原样保留，别被后面的人简化掉：**

> **"框内 r>3 = 0"只能读作"圆柱内为 0"，不能读作"画面里干净"。**

team-lead 指出这条要单独立出来：native-brush 的"框内 = 0"是**构造性结论**（框按分轴 p95≈2.05
算，而远场判据 `r>3` 本就在框半径之上，0 是几何预定的）。真正的问题是视锥 —— 上面算出来
竖屏下视锥竖直半高 ≈5.18 远大于 v95≈1.68，所以远场点**确实进了画面**。下次任何人再引用
"框内为 0"，都应该先看到这句限定。

占比（team-lead 补算）：2,638/137,136 = **1.92%**、4,253/300,326 = **1.42%** ——
绝对数涨了 1.61×，但占比降了（总数涨了 2.19×）。与"44 帧随步数没有变坏"同向。

**⚠ 这两个数同样是 yaw=30 单一视角的**（和上面雾团指数同一个毛病，一并补齐）。
"视锥内 r>3"绕 yaw 一圈（初始 / 最坏 / 中位）：

| 组 | 初始 yaw30 | 绕圈最坏 | 绕圈中位 |
| --- | --- | --- | --- |
| 11帧/2000 | 6 | 16（yaw 80°） | 4 |
| 44帧/2000 | 103 | 336（yaw 180°） | 92 |
| 11帧/6000 | 36 | 431（yaw 185°） | 43 |
| 44帧/6000 | 2,638 | **5,613（yaw 190°）** | 2,634 |
| 44帧/13000 | 4,253 | **12,159（yaw 190°）** | 4,871 |

最坏视角稳定落在 **yaw ≈ 180°–190°**（转到背面，远场整片夹在相机与主体之间），
是最初视角的 **2.1×–2.9×**。折算占比：44帧/6000 从 1.92% 升到 **4.09%**、
44帧/13000 从 1.42% 升到 **4.05%** —— 两组都逼近 5% 那条线（但仍在下方；
且 `r>3` 本身是绝对阈值，见 §9.5.10，别把这个读数当判据）。
**定性结论不受影响**：远场点确实进画面，且远多于"圆柱内为 0"所暗示的。

#### 雾团指数：计数不能当强度，这条推翻了上面"计数小 = 看不见"的读法

team-lead 指出：700 个 α=0.05 的大片和 700 个 α=0.9 的大片，计数都是 700，观感完全不同。
所以 `occlusion_stats.py` 加了**累计雾团指数**：

```
alpha       = sigmoid(opacity)            # PLY 存的是 logit
占屏比      = 2π·σ1·σ2 / (4·tX·tY·depth²) # alpha 加权面积 / 屏幕面积，单贡献封顶 1
               σ 用 exp(scale_*)          # PLY 存的是 log
累计雾团指数 = Σ alpha × 占屏比           # ≈ 屏幕平均新增不透明度
```

锚点：**1.0 ≈ 整屏被一层不透明覆盖，0.1 ≈ 能看出来的量级**。
两种尺度口径都给：`σ1σ2` = 两个最大轴（朝向最不利，**上界**）、三轴几何平均（**典型**）。
真值在两者之间，取决于每个片相对视线的朝向 —— 细长针状片会被上界高估、被典型低估。

初始视角（yaw30，竖屏 asp=0.45）：

| 组 | 危险 splat | 雾团指数 上界 / 典型 | 最严重单个占屏比 | 前方/全视锥 | 巨型片* |
| --- | --- | --- | --- | --- | --- |
| 11帧/2000 | 9 | 0.527 / **0.133** | 0.418 | 7.6% | 3（都在 r<5） |
| 44帧/2000 | 113 | 1.698 / **1.180** | 1.000 | 36.8% | 4（全在 r>5） |
| 11帧/6000 | 113 | 1.120 / **0.337** | 1.000 | 21.6% | 0 |
| 44帧/6000 | 279 | 1.604 / **0.499** | 1.000 | 38.5% | 2（r 最远 22.26） |
| 44帧/13000 | 726 | 1.094 / **0.535** | 1.000 | 23.1% | 3（r 最远 17.47） |

\* 巨型片 = `exp(scale)_max > 3 且 alpha > 0.5`，五组分别 3 / 4 / 0 / 2 / 3 个。

**三条读数（方向和我上一轮给的相反，请注意）：**

1. **计数小不等于看不见。** 五组的典型雾团指数 0.133 / 1.180 / 0.337 / 0.499 / 0.535，
   **全部 ≥ 0.13，都在"能看出来"的量级之上**。我上一轮拿"0.15%–0.24%、不透明的个位数"
   去安抚 apk-shell 是**错的** —— 那是计数，不是强度。这条已单独去信道歉更正。
2. ~~**雾团由极少数巨型片主导，不是统计现象。**~~ **（这条已被 §9.5.13 推翻，只在那批默认参数产物上成立。）**
   当时的证据是 44帧/2000 的 #10522：`σ=(0.69, 3.46, 10.90)、α=0.74、质心半径 8.58、depth 1.42`
   —— 一个**贴在镜头前 1.4 单位处、把整屏糊掉 74%** 的巨型半透明薄片，一人贡献 0.738（**占 89%**）。
   **但那是默认参数那批的产物；生产参数下 top-1 只占 17–30%（§9.5.13）。**
   所以我据此推出的"可枚举、可摘的离散问题"**不成立**，一并撤回。
3. **趋势仍然支持"加长训练不恶化"**：44 帧 2000→6000→13000 典型值 1.180 → 0.499 → 0.535
   （先降后平）。所以撤回"降步数"那一条**仍然成立**，只是理由换了：**不是因为它不脏，
   而是因为脏的程度不随步数变差**。

**一个设备相关的坑（顺手发现，值得真机验证）**：竖屏 `dist≈10–12`、方屏 `dist≈4.5–5.6`，
而巨型片多在 `r>5`。**竖屏相机站得更远，巨型片更容易落在相机与主体之间；方屏相机近，
它们跑到相机背后去了**（44帧/2000 在 asp=1.0 下雾团指数只有 0.383，而且 #10522 根本不在画面里）。
也就是说**这个现象是真机竖屏特有的，桌面/方屏预览看不到**。

### 9.5.9 消融数据集 `ds_44_nofar`：把"离群点数"和"视角数"分开（不重跑 SfM）

native-brush 想要一组"44 帧但离群点少"的数据来拆混杂变量。
**重跑 SfM 这条路我这边不通**：没有可用安卓设备（真机未到、模拟器按 team-lead 要求不动），
换成主机编译又要重新从源码编 OpenCV —— 那正是 team-lead 叫停过的路。

但**有一件比"调阈值重跑"更干净的事可以做**：事后摘点。
工具 `native/sfm/tools/ablate_far_points.py`，产物 `native/testdata/ds_44_nofar/`：

- 删掉那 52 个远场点（质心半径 >3，最远 19.1185），`images.bin` 里对它们的引用置成
  COLMAP 的 -1 哨兵（改动 **136 条** / 全部 61,713 条；原本已是 -1 的 25,942 条不动）；
- **位姿、内参、图像、其余 7,644 个点一律不动**；被删点的 track 长度 2–9，合计 136；
- 自检：观测数两侧一致（35,635 = 35,635，原数据集 35,771 = 35,771，差 136），悬空引用 0，44 帧全注册。

**为什么这比"提高三角化阈值重跑"好**：调阈值不是单变量 —— 三角化接受点集、BA 目标函数、
甚至注册帧数都可能跟着变，产出的是"另一套点云"而不是"少了 52 个点的同一套点云"。
事后摘点只动一件事。

**代价（必须一起说）**：这是 **oracle 过滤**，真实阈值做不到只摘这 52 个，
所以它是**收益上界** —— 如果连上界都没差别，那阈值怎么调都没用；反过来，如果上界有差别，
也不能直接说"改阈值就能达到"。

配上已有的三组即可凑成 2×2（训练长度固定 6000 步）：

| | 11 视角 | 44 视角 |
| --- | --- | --- |
| 0 远点 | `ds_11`（有） | **`ds_44_nofar`（本轮产出）** |
| 52 远点 | —（不存在） | `ds_44`（有） |

### 9.5.10 尺度无关远场占比（五组全量）+ "p95 是否已被污染"这个问题本身不成立

apk-shell 要一个能跨数据集对齐的远场口径（他原来用的 `r>3` 是绝对世界单位）。
工具 `native/sfm/tools/far_fraction.py`，阈值改成 `r > k·p90`（p90 从数据里来，跟着尺度走）。
**k 仍是自由参数**，所以 1.25/1.5/2/2.5/3/4/5 全打；这里摘 1.5/2/3 三个：

| 组 | p90 | p95 | p95/p90 | r>1.5·p90 | r>2·p90 | **r>3·p90** |
| --- | --- | --- | --- | --- | --- | --- |
| 11帧/2000 | 1.3706 | 1.4847 | 1.083 | 1.309% | 0.696% | **0.365%** |
| 44帧/2000 | 1.7194 | 1.9384 | **1.127** | 2.472% | 1.499% | **0.360%** |
| 11帧/6000 | 1.4522 | 1.5923 | 1.096 | 1.610% | 0.956% | **0.529%** |
| 44帧/6000 | 1.9168 | 2.5625 | 1.337 | 4.483% | 3.319% | **1.224%** |
| 44帧/13000 | 1.9041 | 2.5714 | 1.350 | 4.383% | 3.296% | **1.204%** |

（口径：SfM 点云质心。换 splat 自身质心（apk-shell 的口径，与 `plyFraming()` 一致）
后 44帧/6000 是 1.241%、44帧/13000 是 1.385%，与他报的 1.236% / 1.458% 差 0.4% / 5%
—— **跨工具的口径噪声在 5% 量级**，见下文。）

**这条数据推翻的东西，比它支持的更多：**

**① "1.33 是 44 帧这个数据集本身的形态" —— 假的。**
44帧/2000 的 `p95/p90` 是 **1.127**，跟 11 帧那两组的 1.083 / 1.096 同一水平。
1.33 是**同一数据集内**步数从 2000 拉到 6000 才出现的。所以它不是数据集形态，
是**数据集 × 步数的交互**。两个变量单独都不够，而我们只有 5 个点，标定不了交互项。

**② 但"随步数单调上升"也停了**：6000 → 13000 是 1.337 → 1.350（+1%），
尺度无关占比 4.483% → 4.383%（**略降**）。apk-shell 用他自己的口径也得到同样的结论
（4.378% → 4.274%）。**"单调上升 → 13000 会越 5%"是集体外推错了。**

**③ 最要紧的一条：`p95/p90` 那 0.04 的差是噪声，不是信号。**
apk-shell 报 44帧/6000 → 13000 是 1.377 → 1.330（"降了"），我同口径算 1.345 → 1.371（"升了"）。
**方向相反，而幅度都在跨工具口径噪声（5%）之内。** 所以别拿它做趋势判断，只配做日志。

**④ "p95 到底有没有被污染" —— 这个问题在这些数据里没有是/否答案。**
`s > 1-q 则 q 分位失效` 这条规则是我从分位数定义推的、严格成立，**前提是"远场"是一个
可分离的点群**（有定义良好的占比 s）。我去验了它是不是：按 log2(半径) 从 p50 到 max 分 12 格，
44帧/6000 的密度是

```
0.2190 0.1506 0.0735 0.0141 0.0149 0.0149 0.0050 0.0042 0.0032 0.0004 0.0002 0.0001
```

**单调衰减、没有第二个峰** —— 远场不是一个"簇"，是一条连续的长尾。
那么"远场占比 s"就没有独立于 k 的定义：k 取 1.5 是 4.48%，取 3 是 1.22%，
**规则检测到的其实是 k，不是数据**。

**所以正确用法是**：这条规则只能当**设计约束**（"你想让主体占住画幅，就得保证取景分位
落在你认定的主体那一群里"），**不能当测量判据**（"测一下就知道 p95 破没破"）。
自动降档不该建在这个测试上 —— 它的前提在数据里不成立。

**替代方案（我的建议）**：判据直接用**入画率**（投影后落在画布内的采样点占比，
apk-shell 的 `ply_quantile_report.mjs` 已经在算）。它是**直接量**、尺度无关、
不需要 k、也不需要跨数据集常数；代价是"甜区"是一个产品判断（他要 97–98%），
而且必须和"占宽"配对看 —— 只追入画率会被"相机拉远到 100%"这个退化解骗到。
`p95/p90` 保留，但**只打日志不做触发**。

**对档位的结论（与 apk-shell 一致）**：精档回退 p95。两条依据 ——
(a) 精档会越线的外推已被②推翻；(b) 直接量说 p90 更差（入画率 q=0.95 时 95.96–99.61%，
q=0.90 时 93.81–97.60%，落到"裁切多"那一侧）。

### 9.6 六类指标陷阱与辨别法（team-lead 归纳；第六类出自本轮）

这一节不记录结论，记录**怎么在写下一个指标之前先躲开坑**。前五类是前面几轮踩出来的，
第六类是本轮把"远场占比"这个量拆开之后新发现的。

| # | 陷阱 | 一句话 | **辨别法（要问的问题）** |
| --- | --- | --- | --- |
| 1 | 参照盒被撑大 | 拿 bbox/min/max 当尺度判据，被少数离群点撑大数倍 | 参照集里最远的 1% 拿掉，结论变不变？ |
| 2 | 混用口径 | 绝对阈值 / 相对倍数 / 不同质心混着比 | 两边用的是同一把尺子吗？ |
| 3 | 反向子群抵消 | 总数看着没变，构成已经反了 | 拆子群再看一遍 |
| 4 | 判据自指恒为常数 | 量由构造式决定，永远等于某个值（如"p-q 壳占画布宽"恒 = `fill`） | 这个量**有可能**不等于那个值吗？ |
| 5 | 构造性指标 | 阈值取在参照集之外，结果恒为 0（如"取景圆柱内 r>3 = 0"） | 这个量**有可能**不等于 0 吗？ |
| 6 | **参数化伪指标** | 量的值主要由外部参数 k 决定，不由数据决定 | **换一个合理的 k，结论会不会翻？** |

**第六类的实例**：同一份 44帧/6000，"远场占比"取 `k=1.5·p90` 是 4.48%，取 `k=3·p90` 是 1.22%
—— **差 3.7 倍，而且两个 k 都说得通**。所以这个量测到的是 k，不是数据。
注意它和第五类的辨别法不同：第五类问"能不能不为 0"，第六类问"换个 k 会不会翻"。

**归一化只解决一半**：把 `r>3` 换成 `r>k·p90` 解决了"跨数据集不可比"（尺度无关），
**但没有解决"值由 k 决定"**。所以归一化后的占比仍然只能比**排序**，不能读**数值**。

**一条纪律（本轮真正起作用的那一步）**：
**定义上严格成立的规则，也要先验它的前提。**
`远场占比 s > 1-q 则 q 分位失效` 是从分位数定义推出来的、数学上没有漏洞，
但它的前提是"远场是一个可分离的点群"。我去验了：log2(半径) 从 p50 到 max 分 12 格，
密度单调衰减、没有第二个峰 —— **"远场"是我们脑子里的概念，不是数据里的结构**。
前提一倒，规则就只能当设计约束，不能当测量判据。

#### 入画率的边界，以及"参照集该怎么定义"

判据改用入画率之后，有一个必须一起写下来的限定：

- **入画率只判断"有没有裁切"，不判断"主体大小"。** 相机拉到极远时入画率 → 100%，
  但主体小到看不见。**单独用会漏掉"太远"这一侧**，必须配一个主体占画幅的直接量。
- 配套量怎么选，真正的分界**不是"分位数 vs 阈值"，而是"有没有自然的枚举序列"**：
  - **分位数定义的参照集**：q 是无量纲的排序位置，取值随 q 连续变化、
    **可单调枚举**（0.90 / 0.95 / 0.99），一眼能看出稳不稳。
    例如 apk-shell 用的"3D 半径 ≤ p95 的固定参照集的投影占宽"。
  - **倍数 / 绝对阈值定义的**：k 带量纲或任意尺度，**没有自然枚举序列**，
    换档横跳时无从判断该信哪档 —— `r>3`、`r>k·p90` 这类（见第六类）。
  - **单档计数**：无论定义方式，只报一个点的都直接不可信。
- 一句话：**参照集要长在数据上（分位数），不要长在参数上（倍数、绝对阈值）。**

**⚠ 上面那句话容易被用过头，必须配这句一起读（apk-shell 指出，我确认）：**

> **"分位数定义的可用" ≠ "不用做敏感度扫描" —— 分位数本身也是参数（q）。**
> 两条纪律是**叠加**关系，不是替代：**先选"长在数据上"的定义方式（分位数），
> 再按"至少两档"扫一遍。只做第一步不够。**

实例就是我自己的雾团指数：它用的是**分位数定义的**参照（"主体前" = `depth < dist − r3_q`），
按上面的分界属于"可用"那一类，但**它仍然有 q 这个旋钮**。扫三档的结果（44帧/6000，典型口径）：

```
q=0.90   q=0.95   q=0.99
0.585    0.499    0.054
```

**在 q=0.99 上它翻了**（0.499 → 0.054，"明显"掉到"隐约"），只是 q=0.99 本身不合理
（等于把尾部也算进"主体"）才不影响结论。**可见"选对定义方式"并不能免检** ——
分位数只是让枚举变得自然，不代替枚举本身。

### 9.5.11 两套独立实现的交叉比对（我：查看页取景相机；native-brush：训练相机光栅化）

team-lead 要两套独立实现比对。结论是**方向一致、数值不可直接比**，原因值得写下来。

**我这边补了饱和版**（`occlusion_stats.py::saturated()`）。线性求和会高估重叠：
十片各盖 10% 屏的 α=0.74，线性求和是 7.4，真渲染里每点透射率最多降到 0.26。
饱和版按"各片在屏上随机独立覆盖"折：`屏幕平均透射率 ≈ Π(1 − α_i·f_i)`。

初始视角（yaw30，竖屏 asp=0.45），**饱和 / 典型**口径：

| 组 | 线性 典型 | **饱和 典型** | native-brush（前景遮挡占屏） |
| --- | --- | --- | --- |
| 11帧/2000 | 0.133 | **0.126** | — |
| 44帧/2000 | 1.180 | **0.834** | 0.271（α>0.1）/ 0.113（α>0.5） |
| 11帧/6000 | 0.337 | **0.291** | — |
| 44帧/6000 | 0.499 | **0.399** | 0.269 / 0.157 |
| 44帧/13000 | 0.535 | **0.449** | 0.271 / 0.160 |

**为什么数值不能直接比**：这两个量**不是同一个东西**。
我的是**屏幕平均新增不透明度**（alpha 加权、连续量）；
native-brush 的是**屏幕面积占比**（前景累积 alpha 超过阈值的像素比例，且带 α>0.1 / α>0.5 两档）。
一个是"平均多浓"，一个是"多大面积算被挡"，量纲不同，强行对齐数字没有意义。

**可以比的只有两件事，两边都一致：**
1. **雾团是真的**（不是计数错觉）—— 两边都给出非零、且在"看得出"量级的结果。
2. **不随步数变差** —— 我这边是 0.834 → 0.399 → 0.449（先降后平），他是 0.271 → 0.269 → 0.271（持平）。
   **两边都否掉"为画面干净而降步数"这个动作。**

**两边不一致的一处，以及它的成因**：44帧/2000 我给 0.834，他给 0.271。
差异主要在我这一侧被**单个 splat** 主导（#10522 一人贡献 0.738/0.834 = 89%，但见下）。
所以"我这边 2000 步高、之后降"这个**趋势不能当趋势用**。
**稳妥的表述（§9.5.13 订正版）：雾团真实存在、由一批中等飞点的集体尾部构成（生产参数下
top-1 仅 17–30%，不是个体主导）、且不随步数变差。**

**native-brush 那组自己就做了第六类检验**：α>0.1 得 0.271、α>0.5 得 0.113（**差 2.4 倍**）——
数值同样依赖 k，但**"三档持平"这个结论在两档阈值下都成立**，所以他的结论过了检验。
这也提醒：**报告一个参数化量时，至少给两档 k**，让读者自己看结论翻不翻。

#### 雾团指数也是"模型 × 视角"的属性（我这边同源的第三处残留）

apk-shell 连查三处同源残留，共同点是**把入画率当成"模型的属性"在记，而它其实是"模型 × 视角"的属性**。
我照这条去查自己，**雾团指数有同一个毛病**：上面那些数全是**默认视角 yaw=30** 的，
而用户能拖着转。绕 yaw 一圈（72 个朝向，饱和 / 典型口径）：

| 组 | 初始 yaw30 | 绕圈最坏 | 绕圈中位 |
| --- | --- | --- | --- |
| 11帧/2000 | 0.126 | **0.527**（yaw 80°） | 0.067 |
| 44帧/2000 | 0.834 | 0.834（就是 yaw30） | 0.039 |
| 11帧/6000 | 0.291 | **0.438**（yaw 55°） | 0.194 |
| 44帧/6000 | 0.399 | **0.942**（yaw 5°） | 0.026 |
| 44帧/13000 | 0.449 | **0.924**（yaw 0°） | 0.035 |

**同一个模型，转一圈能从 0.026 走到 0.942（36 倍）。** 而且**默认视角常常不是最坏的那个**
（11帧/2000：初始 0.126，最坏 0.527，差 4 倍，出现在 yaw=80°）。

**所以我上一段"五组全部 ≥0.13、都在能看出来的量级"要加限定**：那是**默认视角下**的陈述。
按绕圈中位只有 0.026–0.194（五组里三组低于 0.1 锚点）。稳妥表述改成：

> **默认视角下雾团明显；转一圈的中位大多在"隐约"及以下，但存在把画面糊到 0.92–0.94 的角度。**

**对真机验证的直接含义**：apk-shell 原计划"首屏看一眼主体前方有没有糊层"**不够** ——
首屏不是最坏视角。真机要看就**转一圈看**，尤其留意 yaw 0°–10° 和 55°–80° 这两个区间
（上表最坏值都落在那里）。

### 9.5.12 生产口径复核（替换 9.5.8–9.5.11 里那两栏 44 帧基线）

**⚠ 9.5.8 / 9.5.10 / 9.5.11 里的 `44帧/6000 = 137,136 splats`、`44帧/13000 = 300,326 splats`
不是生产口径。** native-brush 查清：那批跑法用的是旧参数集（`growth5400` 等），
而 `PipelineManager.paramsFor()` 的生产快档基线是 **80,948 splats**、精档 **89,742**。
**那些数只能作组内对照和趋势，不能当"真机会看到什么"的预测。** 生产口径如下。

我用 `occlusion_stats.py` 独立复核 `D:/brushsweep/prod_*.ply`（ds_44 质心、asp 0.45、
线性/典型口径，与 native-brush 汇报口径一致）：

| 配置 | splats | 雾 初始 | **雾 绕圈最坏** |
| --- | --- | --- | --- |
| 无正则（生产快档基线） | 80,948 | 0.266 | **0.984** |
| 1e-5 + aux1.0（第 1 次） | 60,338 | 0.018 | **0.039** |
| 1e-5 + aux1.0（第 2 次） | 60,884 | 0.193 | **0.051** |
| 1e-6 + aux1.0（第 1 次） | 76,524 | 0.127 | 0.089 |
| 1e-6 + aux1.0（第 2 次） | 77,030 | 0.059 | **0.929** |
| 3e-6（单跑） | 72,000 | 0.309 | 0.312 |

**六个数逐位复现**（我 0.2657/0.9844、0.0181/0.0392、0.1933/0.0511、0.1266/0.0888、
0.0592/0.9290，对他 0.266/0.984、0.018/0.039、0.193/0.051、0.127/0.089、0.059/0.929）。
两套独立实现对齐，这批数可以采信。

**选型依据（我复核后认同 1e-5，但机制解释已换 —— 见 §9.5.13）**：
~~1e-6 的第二次跑漏放了一个巨型片~~（这是机制猜测，已被 §9.5.13 推翻）。
改成**只陈述可复现的事实**：1e-6 两次跑的绕圈最坏是 **0.089 / 0.929**，1e-5 两次跑是
**0.039 / 0.051** —— 后者两次都低。**判据用"绕圈最坏"而不是"初始视角"。**

**但有一条方法论必须一起记下来**：雾团指数**单跑方差极大**，而且**两个视角口径都不稳定**：

- 1e-5 两次：初始 0.018 / 0.193（**差 10×**），最坏 0.039 / 0.051（差 1.3×）
- 1e-6 两次：初始 0.127 / 0.059（差 2.1×），最坏 0.089 / 0.929（**差 10.5×**）

所以**任何单点配置的雾团数都不能用于排序**（3e-6 那档 0.309 没落进单调序列，我倾向是噪声，
但没法证明）。能用的单调量只有 **splats / p50(σ) / loss** —— 三者对正则强度都单调，
这也是 native-brush 定 1e-5 的实际依据，不是靠雾团数。

**我的边界**：这几张图（`D:/brushsweep/trainpose_prod_reg.png` 等）我看得到但**评不了画质** ——
我只能算几何。loss 在 1e-5 档仅 −2.4%，splat 数 −25%，**"少掉这些 splat 之后画面还剩什么"
要真机首跑肉眼确认**。

### 9.5.13 "由少数个体主导"被推翻：生产参数下 top-1 只占 17–30%

native-brush 把"最严重单个 splat 的贡献占比"算了出来，我逐档复核**完全吻合**：

| 配置 | 雾（线性上界） | top-1 贡献 | **top-1 占比** |
| --- | --- | --- | --- |
| 无正则 | 0.7732 | 0.1478 | **19.1%** |
| 1e-5 + aux1.0 ① | 0.5028 | 0.1494 | **29.7%** |
| 1e-5 + aux1.0 ② | 1.1674 | 0.3170 | **27.2%** |
| 1e-6 + aux1.0 ① | 0.5030 | 0.0985 | **19.6%** |
| 1e-6 + aux1.0 ② | 0.7505 | 0.1300 | **17.3%** |
| 3e-6 | 1.3119 | 0.2401 | **18.3%** |

上表是 **初始格（yaw 30°）× 上界口径**。native-brush 实测吻合。

**⚠⚠ 但 top-1 占比本身是"格位 × 口径"双相关量（native-brush 发现，我跑全后确认）。**
我把 6 档 × 3 格位（初始 / 绕圈最坏 / 绕圈中位）× 2 口径的 top-1 占比全列（%）：

| 配置 | 初始·上界 / 典型 | 最坏·上界 / 典型 | 中位·上界 / 典型 |
| --- | --- | --- | --- |
| 无正则 | 19.1 / 24.7 | 20.3 / 35.1 | 36.3 / 24.7 |
| 1e-5+aux1.0 ① | 29.7 / 12.7 | **70.8 / 66.7** | 9.1 / 8.4 |
| 1e-5+aux1.0 ② | 27.2 / **87.9** | 45.7 / **86.3** | 23.9 / 28.8 |
| 1e-6+aux1.0 ① | 19.6 / 16.2 | 43.8 / 36.8 | 14.1 / 13.3 |
| 1e-6+aux1.0 ② | 17.3 / 7.4 | 22.5 / 42.6 | **59.4** / 27.6 |
| 3e-6 | 18.3 / **77.7** | 17.3 / **76.9** | 4.8 / 5.5 |

**"是不是个体主导"这个问题没有档位级答案**：同一档（1e-5②）在典型口径下 86–88%（强个体主导），
在上界口径下 23–46%（非个体主导）；3e-6 在初始/最坏格典型口径 77%，中位格只有 5.5%。

**所以我 §9.5.13 上面那句"六个档全都不是个体主导"要再加限定**：
它只在 **上界口径 × 初始格** 成立；换格位或换口径，个体主导在该格位**可以成立**。
此前"由极少数巨型片主导"的印象来自默认参数那批的 #10522（一人 89%），那批不是生产口径
（§9.5.12）——这一条仍然成立，但**推翻的只是"档位级普遍结论"，不是"任何格位下都非个体主导"**。

**被推翻的两条（都在我文档里，现予撤回）：**
1. §9.5.8 第 2 条"雾团由极少数巨型片主导，不是统计现象"，以及由它推出的
   **"可能是一个可枚举、可摘的离散问题"** —— 上界口径·初始格下 top-1 只有 17–30%，
   其余是**几十上百个中等飞点的集体尾部**。**（限定见上表：仅该格位/口径成立。）**
2. §9.5.12 里"1e-6 第二次跑漏放了一个巨型片"这个**机制解释** ——
   1e-6 那次 0.929 不是漏放一片造成的。**机制是编的，改成只陈述事实。**

**⚠ 但"不要按个体去删片"这个处置结论保留，理由要换**（不能再靠"top-1 只有 17–30%"）：
即使某格位下 top-1 高达 66–88%，**主导者本身随格位换人**——在 yaw 0° 主导的那一片，
转到中位格就不是它（3e-6 中位格典型口径 top-1 只剩 5.5%）。
所以"按个体删"无效的原因不是"没有主导者"，而是**"主导者是格位的函数，不是模型的属性"**：
删掉任一片只修好一个朝向，其余朝向照旧。处置结论不变，依据换成这一条。

**保留的**：单跑差异大（**观测到** 10× 级）成立；"稳定性只能靠复跑、不能靠换视角口径"成立。
⚠ 措辞限制见 §9.5.14：所有倍差都是 **n=2 的观测极差**，不是方差估计，
应读作"这两次跑差了 10×"而非"方差是 10× 级"；要断言不可复现需 n≥3。

**对处置的含义（和 team-lead 已撤回的"巨型片计数"是同一类伪指标）**：
**不要按个体去删片。** 正确做法是 native-brush 给的：**复跑取中位数 / 看单调量
（splats、p50(σ)、loss）**。生产参数下的 1e-5 正是靠这三条定的。

**方法论留档**："雾团是被一个片还是一群片撑起来的"这个问题**必须每次实测**，
top-1 占比已加进 `occlusion_stats.py` 输出 —— 印象里那个 89% 只属于那批默认参数产物。

**真机 pull 回 `final.ply` 后的离线复核路径（待办，等真机）**：
native-brush 的 `native/tools/viewrender.py` 可直接出查看页视角的图（含角度参数），
能与他已产出的 `D:/brushsweep/view_prod_base.png`（糊）/ `view_prod_1e5.png`（清）并排比。
**口径提醒**：我的雾团指数衡量的是**屏幕覆盖率**，native-brush 的 p50(σ) 衡量的是**尺寸**，
两者都不衡量"主体清不清楚"（清晰度）。所以"雾压到 0.04"**不能**代替
"近距离看主体清不清楚"这一条 —— 后者是 p50 −52% 的唯一对应观测，真机清单里必须单列。

### 9.5.14 回退档 1e-6 的雾必须带口径（单点值 0.147 会误导）

native-brush 给的回退路径是"主体变稀疏 → `scaleLossWeight` 改 `1e-6`"，附带两个数
（雾团 0.147、p50 只 −15%）。**"雾团 0.147"是缺口径的单点值，且来自已作废的默认参数批次**
（native-brush 已确认并更正）。我按 §9.5.13 的口径重跑了 1e-6 两档：

| 1e-6 跑次 | 初始 yaw30 | 绕圈最坏 | 绕圈中位 |
| --- | --- | --- | --- |
| ① 上界 | 0.5030 | **1.1499**（yaw 0°） | 0.0861 |
| ① 典型 | 0.1266 | 0.0888 | 0.0044 |
| ② 上界 | 0.7505 | **2.5122**（yaw 10°） | 0.1172 |
| ② 典型 | 0.0592 | 0.9290 | 0.0139 |

**⚠ 我自己在这里犯过一次错（已更正）：** 初稿拿 1e-6 的**最坏**去比无正则的**初始**
（2.51 > 0.77），不同格不可比。基线的最坏我也实测了：上界 **1.7045**（yaw 15°）、
典型 **0.9844**（yaw 15°）。同口径重比如下（全部取**绕圈最坏**）：

| 档位 | 最坏·上界 | 最坏·典型 | 对基线 |
| --- | --- | --- | --- |
| 无正则（基线） | 1.7045（yaw 15°） | 0.9844（yaw 15°） | — |
| 1e-5 + aux1.0 ① | 1.2721（yaw 0°） | **0.0392**（yaw 0°） | 上界 −25%，典型 −96% |
| 1e-5 + aux1.0 ② | 1.2693（yaw 340°） | **0.0511**（yaw 340°） | 上界 −26%，典型 −95% |
| 1e-6 + aux1.0 ① | 1.1499（yaw 0°） | 0.0888（yaw 0°） | 上界 −33%，典型 −91% |
| 1e-6 + aux1.0 ② | **2.5122**（yaw 10°） | 0.9290（yaw 10°） | 上界 **+47%（更差）**，典型 −6%（相当） |

**准确结论（native-brush 与我一致）**：1e-6 在最坏视角上**没有任何可靠改善** ——
上界口径下 ② 反而比基线更差（2.51 vs 1.70），典型口径下 ② 与基线相当（0.93 vs 0.98）。
这比"稍微差一点"更强：**回退档买不到雾的改善**。

**另外一条新发现（加强上条）**：1e-6 两次跑在最坏视角上**自身就差 10×**
（典型 0.0888 vs 0.9290），说明**回退档的雾连自身都不稳定**，又一次落在"单跑方差 10× 级"
（§9.5.12）上。对比之下 1e-5 两跑最坏是 0.0392 / 0.0511，**高度一致** ——
这也是 1e-5 优于 1e-6 的又一独立证据（不依赖雾的绝对值，只依赖复现性）。

**⚠ 但"稳定/不稳"是格位属性，不能贴到档位上（native-brush 更正，我接受）。**
我把 6 个格位（初始/最坏/中位 × 上界/典型）的**两跑倍差**（大/小）全列出来：

| 格位 | 1e-5 两跑 | 倍差 | 1e-6 两跑 | 倍差 | 谁更稳 |
| --- | --- | --- | --- | --- | --- |
| 初始·上界 | 0.5028 / 1.1674 | 2.32× | 0.5030 / 0.7505 | 1.49× | **1e-6** |
| 初始·典型 | 0.0181 / 0.1933 | **10.68×** | 0.1266 / 0.0592 | 2.14× | **1e-6** |
| 最坏·上界 | 1.2721 / 1.2693 | 1.00× | 1.1499 / 2.5122 | 2.18× | **1e-5** |
| 最坏·典型 | 0.0392 / 0.0511 | 1.30× | 0.0888 / 0.9290 | **10.46×** | **1e-5** |
| 中位·上界 | 0.0714 / 0.0870 | 1.22× | 0.0861 / 0.1172 | 1.36× | 1e-5（微弱） |
| 中位·典型 | 0.0082 / 0.0034 | 2.41× | 0.0044 / 0.0139 | 3.16× | 1e-5（微弱） |

**复现性的排序在 6 个格位里翻了两次**（初始格 1e-6 更稳，最坏格 1e-5 更稳）。
所以准确规则是：**先固定格位，再看该格位的复现性；决策用最坏格，就只看最坏格。**
不能倒过来说"1e-5 稳定、1e-6 不稳"——那会让人误以为 1e-5 在初始格的单次数也能用，
而那个格子它照样差 10.68×。这与执行约束第 4 条"两边必须取同一格"是同一条纪律的延伸：
**同一格内比复现性，跨格不比任何东西。**

**⚠⚠ 样本量限制（我自己加的）**：以上所有"倍差"都是 **n=2 的观测极差**，不是方差估计。
两个样本的极差本身波动极大（对数正态下单次极差轻松到 3–10×），所以
"差 10×"只能读作"这两次跑差了 10×"，**不能**读作"这一档的方差是 10×"。
要真正断言某档不可复现，**需要 n≥3**。当前所有复现性结论都带这个限制，
包括 §9.5.12 的"单跑方差 10× 级"——那条同样建立在 n=2 上，措辞应为"观测到 10× 级差异"
而非"方差是 10× 级"。

**结论：回退不是免费的。** 换到 1e-6 买到的是 p50 少削 37 个百分点（−52% → −15%），
代价是最坏视角的雾无改善（上界口径更差），且最坏视角恰好落在 yaw 0–10°
（与真机重点观察段重合）。

**执行约束（已发 apk-shell）**：① 只在"主体清不清楚"被真机确认变稀疏后才回退；
② 回退后必须重看 yaw 0–10°；③ 雾是"模型 × 视角"量，最少看初始/最坏/中位三格，
不许用单一视角单一口径的数代表一整档；④ 比较两个档位时**两边必须取同一格**
（同为最坏或同为初始），我这次就是把最坏比初始了。

#### 9.5.14.1 判据线（≤0.3）本身也是自由参数，必须扫

native-brush 提出把问题从"估方差"（需 n≥3）换成"**判跨线**"（n=2 够）：
判据线 = 绕圈最坏 ≤ 0.3（典型口径）；1e-5 两跑 0.039 / 0.051 都在线下，
1e-6 两跑 0.089 / 0.929 **跨线** → 排除 1e-6 的硬理由是"它有一次越线"，不是"它倍差 10×"。

**换框我接受**（跨线是观测事实，不需要方差估计），**但判据线自己是自由参数**，
按 §9.6 第六类坑的纪律必须扫。我扫了 5 档（典型口径·绕圈最坏）：

| 判据线 | 1e-5 ① 0.0392 | 1e-5 ② 0.0511 | 1e-6 ① 0.0888 | 1e-6 ② 0.9290 | 无正则 0.9844 |
| --- | --- | --- | --- | --- | --- |
| 0.1 | 线下 2.55× | 线下 1.96× | 线下 **1.13×** | 过线 9.29× | 过线 9.84× |
| 0.2 | 线下 5.10× | 线下 3.91× | 线下 2.25× | 过线 4.64× | 过线 4.92× |
| **0.3** | 线下 7.65× | 线下 5.87× | 线下 3.38× | 过线 3.10× | 过线 3.28× |
| 0.5 | 线下 12.76× | 线下 9.78× | 线下 5.63× | 过线 1.86× | 过线 1.97× |
| 1.0 | 线下 25.51× | 线下 19.57× | 线下 11.26× | **线下 1.08×** | **线下 1.02×** |

**扫描结果（比单条线更硬）**：

1. **判决在 (0.051, 0.929) 这整个区间内不变** —— 区间宽 18×。任取其中一条线，
   结论都是"1e-5 两跑均在线下、1e-6② 过线"。**结论不依赖 0.3 这个具体取值。**
2. 线 **≥ 0.93** 时判据失效：连无正则基线（0.984）都判为合格。这被锚点语义否掉
   （1.0 = 满屏不透明；基线是已知"明显"档，必须判为过线）→ **线必须 < 0.984**。
3. 线 **≤ 0.051** 时 1e-5 自己也过线、判决翻转 → **线必须 > 0.051**。

**⚠ 但"用余量替代方差"这一步不成立（我实测后否掉）**：
"1e-5 离线 6×，即使再来 10× 波动也不会跨" —— 实际余量是 **7.65× 与 5.87×**（线 0.3 ÷ 值），
**两者都小于 10×**：0.0511 × 10 = 0.511 > 0.3，**会跨线**。而且本批数据里
**已实测到 10.46× 与 10.68× 两次倍差**，5.87× 的余量**小于已观测波动幅度**，不能称安全余量。
（线取 0.5 时余量升到 9.78–12.76×，才勉强够到已观测波动量级。）

**最终表述应为**：结论的稳健性来自"**判决在整个 (0.051, 0.929) 区间内不变**"（参数扫描得到的
区间稳健性），**不是**来自"余量足够大"（n=2 撑不起的方差断言）。

**⚠ 区间本身也有样本量限制（native-brush 提出，我再收紧一层）**：
区间两个端点都是 **n=2 的点估计**，不是保证区间，不能当置信区间用。准确说法是
"**在已观测取值下**，判决对线的取值在 18× 范围内不敏感"，而非"判决必然在该区间内不变"。

**我再补一层（方向性偏差）**：两个端点分别是
下界 = max(1e-5 两跑) = 0.0511、上界 = max(1e-6 两跑) = 0.9290，
**两端都是"2 个样本的最大值"，而样本最大值是上尾的**低估**估计**。
所以补跑后两端**大概率都会上移**（1e-5 的最大值可能变大 → 下界上抬、区间收窄；
1e-6 的最大值可能变大 → 上界上抬、区间放宽）。**区间的宽与位置都会随 n 变化**，
当前 18× 只是"两次观测给出的区间"，不是它的稳定值。

**另注**：无正则基线的绕圈最坏只有 **n=1**（仅 `prod_fast_base` 一跑），所以"基线过线"是单次
观测，只够用来**校验判据线位置**（要求线 < 0.984），不够用来估计基线自身波动。

#### 9.5.14.2 viewer 合成相机口径的"入画率"几乎全由分位数 q 决定（实测）

apk-shell 新加了 `tools/ply_traincam_framing.mjs`（**训练相机真实位姿 + 真实内参**，
ds_44 / 640×1136 / fovx 26.4°），量得的入画率是 **27–29%**，与 viewer 合成相机口径的
**97–99%** 是两个量、不可互验 —— 这个判断我同意，但**理由要换**，而且比"两个口径"更严重。

我扫了 q（viewer 合成相机的取景距离由 `framingQuantile` 分位半径推出）：

| q | 视锥内（入画率） | 与 q 的差 |
| --- | --- | --- |
| 0.80 | 55102 / 60338 = **91.3%** | +11.3 pp |
| 0.90 | 57864 / 60338 = **95.9%** | +5.9 pp |
| 0.95 | 59603 / 60338 = **98.8%** | +3.8 pp |
| 0.99 | 60137 / 60338 = **99.7%** | +0.7 pp |

**q 从 0.80 扫到 0.99，入画率就从 91.3% 单调走到 99.7%（跨度 8.4 pp，纯由参数决定）。**
所以 apk-shell 报的"97–99%"实际读作"**q=0.95 再 +3.8 pp**"——**这个 Level 不是数据的属性，
是所选分位数的属性**。它与 §9.6 第四类坑（自指常量）同源：取景相机本身就是**从点云拟合出来的**，
点云决定相机、相机再评价点云，于是入画率天然 ≈ q。

**可用之处**：只有**固定 q 之后的相对变化**带数据信息（跨模型 / 跨步数比），
**绝对值不带**。要报就报"q=0.95 下 X 模型比 Y 模型高 n pp"，不要报"入画率 98.8%"。
（残差里确实有形状信息——缺额/(1−q) 在 0.24–0.44 之间不恒定——但那是二阶量。）

**对比之下，训练相机口径的 27–29% 是真 Level**：相机位姿与内参都是给定的，不含拟合参数，
所以它是数据的属性。两套不可互验的原因也应改成：**一个是参数决定的伪 Level，一个是真 Level**，
不是"两个口径不同"。

**顺带给 apk-shell 的一条**：27–29% 是**单相机**口径，回答不了他说的"模型有没有超出拍摄
覆盖范围"。要回答这个，需要的是**全 44 台相机视锥的并集覆盖率**，以及它的**补集
（从未被任何训练相机看到的 splat）**。后者特殊：这些 splat 在训练中拿不到梯度、
不会被正常 densify/prune，是飞点的天然候选——可以直接拿去和 §9.5.13 的雾团贡献者对账，
是个可证伪的检验。

### 9.5.15 训练相机视锥并集与补集：**无 actionable**（team-lead 派工，已按停止条件结案）

工具 `native/sfm/tools/traincam_coverage.py`。数据：`prod_fast_reg.ply`（1e-5+aux1.0 ①，
生产口径）、`ds_44/sparse/0`（44 台训练相机，640×1136，SIMPLE_RADIAL f=1362.3 k1=0.115）、
**n = 1 个模型 / 60,338 splats / 44 台相机**。

**自检**：脚本重算的雾团指数在 yaw=0°、asp=0.45 下为 **上界 1.2721 / 典型 0.0392**，
与 §9.5.14 表中 `occlusion_stats.py` 的原值逐位一致 —— 说明取景相机与各向异性口径复刻无误，
下面的贡献者名单与 §9.5.13 同源。

**定义扫描（"视锥边界怎么算"是自由参数，三档全算）**：
A 中心严格（带径向畸变、无 margin）／B 留 10% margin／C 按 1σ 投影半径放宽。

| 定义 | 并集覆盖 | **补集占比**（数 1） |
| --- | --- | --- |
| A | 60,086（99.58%） | **0.42%**（252 个） |
| B | 60,236（99.83%） | **0.17%**（102 个） |
| C | 60,115（99.63%） | **0.37%**（223 个） |

**重叠率（数 2）**：绕圈最坏视角（yaw 0°）+ 典型各向异性口径下的雾团贡献者，
top-K 取 K = 50 / 200，与补集的重叠率 ——
**A 0.0% / B 0.0% / C 0.0%（两档 K 都是 0）**；
不设 K 时，全体 160 个前方贡献者落在补集的比例也是 **0.0% / 0.0% / 0.0%**。

**判定：无 actionable。** 两条停止条件**同时触发**：
① 补集占比 0.17–0.42% **< 5%**；② 重叠率 0% **< 20%**。

**一句话结论**：这台模型里"从未被任何训练相机看到的 splat"只有 0.2–0.4%，
而且**雾团贡献者一个都不在其中**——飞点不是"训练时没被看到"造成的。
按 team-lead 的边界，机制不再往下挖，也不提任何 prune 策略。

**附带**：三档定义的补集占比同向（都 <0.5%）、重叠率同向（都 0%），
所以这一次结论**不是定义决定的**（与"远场占比"那类相反），可以放心报"无 actionable"。
