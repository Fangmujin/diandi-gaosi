# native-brush —— headless 3DGS 训练引擎

`libbrushheadless.so` 是一个 **PIE 可执行文件**(不是 JNI 库),由 Android 端用
`ProcessBuilder` 以子进程方式调用,读取 COLMAP 数据集、训练 3D Gaussian Splatting、
输出 `.ply`。基于 brush v0.3.0(ArthurBrussee/brush)裁剪而来。

## 1. 产物

| ABI | 路径 | 大小 | Machine | strip |
| --- | --- | --- | --- | --- |
| arm64-v8a | `native/prebuilt/arm64-v8a/libbrushheadless.so` | 70,867,320 字节 | AArch64 | 是 |
| x86_64 | `native/prebuilt/x86_64/libbrushheadless.so` | 78,215,744 字节 | X86-64 | 是 |

sha256:

```
3ac04ee075e80d24d3ee30aad667f4bc21f0f37b01ff08b0518e67fc9f2e55fe  prebuilt/arm64-v8a/libbrushheadless.so
bcc557407a4c42ad4616c30c8e1c3a787017581461d2176d05c8f1e04c44e0d6  prebuilt/x86_64/libbrushheadless.so
```

两个 ABI 均为带 ViewSplats 修复(`6060370`)重建后的产物。

两个产物核验(`llvm-readelf -h/-l/-S`):

```
arm64-v8a: Type: DYN  Machine: AArch64
x86_64  : Type: DYN  Machine: Advanced Micro Devices X86-64
两者均有 INTERP(/system/bin/linker64)、LOAD 对齐 0x4000(16KB,满足 Android 15+)
两者 symtab / debug_info / debug_str 段数量均为 0  -> 已 strip
```

关于体积:78MB 是**真实代码与常量**,不是调试符号。段分布 `.text` ≈ 36MB、
`.rodata` ≈ 31MB,`llvm-strip` 再跑一遍字节数不变,因此无法做到 SfM 那种
100MB → 7MB 的压缩比。APK 侧如需减包,只能靠 split APK / 仅下发 arm64 / 按需下载。

## 2. 构建

```bash
# 前置:Rust 1.99 在 C:/Users/28928/.cargo/bin/cargo.exe(不在 bash PATH 里)
#      NDK 27.2.12479018,SDK 在 C:/Users/28928/AppData/Local/Android/Sdk

cd native/brush
bash build_android.sh x86_64                       # -> ../../prebuilt/x86_64/libbrushheadless.so
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
  bash build_android.sh arm64-v8a                  # -> ../../prebuilt/arm64-v8a/libbrushheadless.so
```

`build_android.sh` 做的事:把 NDK 工具链加入 PATH,设置 `CARGO_BUILD_JOBS=2`、
`RUST_MIN_STACK=67108864`、`CARGO_PROFILE_RELEASE_OPT_LEVEL=2`,按 target 指定
CC/AR/LINKER,`cargo build --release -p brush-headless --bin brush_headless --target <triple>`,
把产物改名 `libbrushheadless.so` 落盘,最后跑 `llvm-strip`。

16KB 页对齐在 `native/brush/.cargo/config.toml` 里对两个 Android target 统一加了
`-C link-arg=-Wl,-z,max-page-size=16384`。

arm64 首次构建遇到 `rustc.exe` 自身崩溃(`0xc0000409 STATUS_STACK_BUFFER_OVERRUN`,
发生在编译 `burn-cubecl-fusion`)。这不是代码错误,是编译器在 Windows 上的栈/内存问题,
处理方式是 `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16` 降低单次 codegen 压力后重试 ——
重试成功,`Finished release profile in 17m 06s`。

另一个坑:构建过程会被沙箱拦截(`rustup` toolchain 目录与 `~/.cargo/registry` 的
file-read 被拒,表现为 `only metadata stub found for dylib dependency std` /
`permission denied when opening file ...tokio-stream...chunks_timeout.rs`)。
这与代码无关,需要在关闭沙箱的环境下执行构建命令。

还有磁盘:`native/brush/target` 全量约 13.5G(`debug` 2.0G + `release` 3.2G +
`x86_64-linux-android` 2.9G + `aarch64-linux-android` 5.3G),而 rustc 的 LLVM
后端在链接期需要数 GB 临时空间。C: 盘余量不足时报
`rustc-LLVM ERROR: IO failure on output stream: no space on device` —— 这是磁盘
问题,不是编译错误。

**推荐解法是让构建完全不写 C:,而不是去腾空间**(腾/删目录既慢又容易中途失败):

```bash
export TMP=D:/brush-tmp  TEMP=D:/brush-tmp      # 连 rustc/NDK clang 的临时文件也挪走
export CARGO_TARGET_DIR=D:/bt2                  # 中间产物落 D:,约 5G/target
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 bash build_android.sh arm64-v8a
```

`build_android.sh` 已支持 `CARGO_TARGET_DIR`(产物路径不再写死 `$ROOT/brush/target`)。

`D:/brush-target` 这个具体路径**不要再用了**:之前中断的 `mv` 把 host 的 `release`
目录副本留在了那里,和 cargo 交叉编译时的 host 构建目录(`$CARGO_TARGET_DIR/release`)
撞车,报 `Failed to clean up ... out\probe: 拒绝访问 (os error 5)` / EXIT=101。
换一个全新的空目录(如 `D:/bt2`)一次就过。**教训:别用 `mv` 搬运 target 目录,
更别搬进任何后续可能作为 `CARGO_TARGET_DIR` 的路径。**

这样即使 C: 只剩几十 MB,`cargo` 依然能正常启动并完成构建;构建过程中 C: 余量会自行回升。

全量构建耗时参考:`CARGO_BUILD_JOBS=2` + `opt-level=2` 下 arm64 走了 86m58s,
x86_64 走了 46m31s(同一个 `CARGO_TARGET_DIR` 下 host 侧依赖可复用,所以第二个 ABI 更快)。
首次在 C: 上因有缓存是 17m06s。耗时长是并行度与 IO 决定的,不是失败征兆,
中途日志只有 `Compiling ...` 属正常。

**换盘时宁可让 cargo 从头编,不要拷贝 target 目录。** 把 `target/<triple>` 用
`mv`/`cp` 搬到另一个盘,cargo 的 fingerprint 全部失效,结果就是"明明有缓存却全量重编"
(本次 arm64 从 17m 变成 87m 就是这个原因)。全新空目录让 cargo 从头编,至少 fingerprint
是自洽的,第二次起增量能真正生效。代价只多付一次,比拷过去再全量重编划算。

## 3. 改动清单

`native/brush/`(fork 仓库,基线 commit `a818e55` = brush v0.3.0):

新增:

- `crates/brush-headless/Cargo.toml`、`crates/brush-headless/src/main.rs`
  —— headless trainer,只依赖 brush-process / brush-dataset / brush-render / brush-vfs,
  **不依赖 egui、winit、rerun**。
- `crates/brush-process/src/control.rs`
  —— `stop` / `pause` 控制文件协议(`should_stop()` / `should_pause()` /
  `set_control_dir()`)。
- `build_android.sh` —— 交叉编译脚本。

修改:

- `crates/brush-process/src/lib.rs` —— 导出 `pub mod control;`
- `crates/brush-process/src/message.rs` —— 新增 `ProcessMessage::ExportStep { iter }`
- `crates/brush-process/src/train_stream.rs`
  - 训练循环每轮检查 `stop` / `pause`;`stop` 时先导出当前 ply 再优雅退出
  - 场景包围盒退化保护:相机全在同一高度时 `bounds.extent` 会有 0 分量,
    采样 `rng.random_range(min..max)` 会 panic,现在把 extent 钳到最大轴的 5%
- `crates/brush-process/Cargo.toml`、`crates/brush-process/src/visualize_tools.rs`
  —— rerun 改为可选 feature `rerun-viz`(默认开),headless 用
  `default-features = false` 关掉,rerun 完全不进依赖图
- `crates/brush-cli/src/lib.rs` —— 补 `ExportStep` 分支,保持 workspace 可编译
- `crates/brush-dataset/src/scene_loader.rs`
  —— `MAX_CACHE_MB` 改为函数 `max_cache_mb()`,可用环境变量
  `BRUSH_MAX_CACHE_MB` 覆盖;Android 默认 1536,wasm 2048,桌面 6144
- `crates/brush-render/src/render.rs`
  —— `get_tile_offsets` 原来写死 `CubeDim::new_1d(512)`,在只支持 256
  invocations/workgroup 的设备上会被后端拒绝,现按 `hardware.max_cube_dim` /
  `max_units_per_cube` 钳制,dispatch buffer 用同一个值
- `crates/brush-headless/src/main.rs`
  —— `ProcessMessage::ViewSplats` 原来是 `anyhow::bail!`,导致**任何带
  `points3D.bin` 的 COLMAP 数据集必挂**(EXIT=1,报错 "contains only ply
  files")。而 SfM 产出的 COLMAP 一定带 points3D,所以这是真机上的必现故障。
  现改为忽略并记日志(与 `brush-cli/src/lib.rs`、`brush-ui` 一致):这些点正是
  初始 splat 的来源,不该被当成错误。
- `Cargo.toml` —— `[profile.release.package.brush-headless] opt-level=1, codegen-units=16`
- `.cargo/config.toml` —— 两个 Android target 的 16KB 页对齐

`native/deps/`(独立 git 仓库,通过 workspace `[patch]` 以 path 依赖接入):

- `deps/cubecl/crates/cubecl-reduce/src/config.rs`
  —— `generate_cube_dim()` 原来固定 `CubeDim::new_2d(plane_dim, 8)`,当设备
  `maxComputeInvocationsPerWorkgroup` 小于 `plane_dim * 8` 时后端直接拒绝创建
  pipeline。现按设备能力钳制 X/Y 两轴。

> 注:`deps/cubecl/crates/cubecl-runtime/src/tune/tuner.rs` 里带 `[marvis patch]`
> 标记的改动是基线快照自带的,不是本次改动。

## 4. SDK 契约(Android 侧唯一依赖)

### 4.1 放置与启动

把 `.so` 放进 `app/src/main/jniLibs/<abi>/libbrushheadless.so`,运行时拷到
`context.getFilesDir()`(或 `/data/local/tmp`),`chmod 755`,再用
`ProcessBuilder` 执行。它是 PIE 可执行文件,靠 linker exec,**不是** `System.loadLibrary`
的目标(参考 ffmpeg-kit 的做法)。

### 4.2 命令行参数

```
--path DIR              COLMAP 数据集目录(images/ + sparse/)
--export-path PLY       输出 ply 路径(中间 checkpoint 覆盖同一文件)
--control-dir DIR       监视 stop/pause 的目录,默认取 --export-path 所在目录
--total-steps N         默认 30000
--max-resolution N      默认 1920
--max-splats N          默认 5000000
--sh-degree N           默认 3
--refine-every N        默认 200
--growth-stop-iter N    默认 15000
--export-every N        默认 500
--seed N                默认 42
--max-frames N          可选,限制加载帧数
--eval-split-every N    可选,每 N 帧抽一帧做 eval
--opac-loss-weight F    默认 1e-9,不透明度正则(压 logit opacity)
--scale-loss-weight F   默认 1e-8,scale 正则(Σ exp(scale),选择性压大 splat,见 5.6.5)
--aux-loss-time F       默认 0.9,辅助正则的作用时长**兼幅度**(见 5.6.5)
```

**注意:这三个正则参数是 2026-10-15 新增透传的。** `TrainConfig` 里本来就有
(brush 上游),但 headless 的 `HeadlessArgs` 只透传了 7 个字段,这三个没透传 ——
所以**旧 .so 上改 Java 侧 `paramsFor()` 传这些参数是无效的**(clap 会报未知参数,
进程直接退出)。必须用包含本次改动重编的 .so(两个 ABI 都要重编)。

**`--path` 的目录布局其实很宽松**(实测结论,见 5.5)。下面两种都行:

```
A) 标准 COLMAP:      DIR/images/*.jpg + DIR/sparse/0/{cameras,images,points3D}.bin
B) 平铺(去掉 sparse/0): DIR/images/*.jpg + DIR/{cameras,images,points3D}.bin
```

两种都能正确加载(10 张图、2153 个点、10 个 training views 全部一致)。
text 格式(`cameras.txt` 等)同样支持。**真正会导致失败的是 `points3D.bin` 存在时
headless 把它当错误** —— 那是 `6060370` 之前的事,现在已修。所以如果你看到
`<path> contains only ply files; a COLMAP dataset directory is required`,
**第一反应应该是"跑的是旧二进制"**,而不是去改目录布局:这条报错字符串在当前源码里
已经不存在了。

### 4.3 进度输出(stdout)

**每行一个 JSON 对象,行缓冲,不使用块缓冲**,字段固定:

```json
{"iter":0,"total":30,"splats":0,"loss":0.0,"elapsed_ms":0,"eta_sec":0.0,"stage":"training"}
```

- `iter` / `total`:当前迭代 / 总步数
- `splats`:当前高斯点数量
- `loss`:loss(NaN/Inf 会被钳成有限值)
- `elapsed_ms`:启动至今毫秒
- `eta_sec`:按每轮耗时外推的剩余秒数
- `stage`:`training` | `exporting` | `done`,失败时为 `failed` 并多一个 `error` 字段
- 日志、panic 全部走 **stderr**,stdout 保持机器可解析

成功结束的最后一条应形如 `stage":"done"` 且 `iter` 接近 `total`。

### 4.4 控制文件(工作目录内)

- 出现 `stop` 文件 —— 立刻导出当前 ply,然后优雅退出
- 出现 `pause` 文件 —— 训练循环挂起(150ms 轮询),文件消失或出现 `stop` 后继续/退出
- 启动时会自动清掉上一次残留的 `stop` / `pause`

### 4.5 环境变量

- `BRUSH_MAX_CACHE_MB` —— 图片缓存上限(MB),Android 默认 1536
- `RUST_LOG` —— 日志级别,建议 `info`
- `CUBECL_WGPU_MAX_TASKS`、`CUBECL_AUTOTUNE_LEVEL` —— cubecl 调优参数,一般不用动

## 5. 验证

### 5.0 真 GPU 上的完整验证(决定性证据)

为了把"模拟器崩是驱动问题、不是我们的问题"从推断变成实测,我在 Windows 宿主上编了同一份
`brush-headless`(目标 `x86_64-pc-windows-msvc`,只换 target,代码与补丁完全一致),
用本机真实 Vulkan(RTX 5060)跑同一个 COLMAP 数据集。

跑法 1 —— 跑满 30 步:

```
RUST_LOG=info brush_headless.exe --path testdata/tiny_colmap --export-path hostrun/out.ply \
  --total-steps 30 --max-resolution 128 --max-splats 50000 --sh-degree 0 \
  --refine-every 10 --growth-stop-iter 25 --export-every 10 --seed 42
```

stdout(节选,逐行 JSON):

```
{"iter":0,"total":30,"splats":0,"loss":0.0,"elapsed_ms":0,"eta_sec":0.0,"stage":"training"}
{"iter":5,"total":30,"splats":10000,"loss":0.0059529142,"elapsed_ms":4573,"eta_sec":22.865,"stage":"training"}
{"iter":10,"total":30,"splats":10000,"loss":0.0059529142,"elapsed_ms":4573,"eta_sec":22.865,"stage":"exporting"}
{"iter":15,"total":30,"splats":10514,"loss":-0.0055032107,"elapsed_ms":5901,"eta_sec":5.901,"stage":"training"}
{"iter":25,"total":30,"splats":11477,"loss":0.0068959272,"elapsed_ms":6553,"eta_sec":1.3106,"stage":"training"}
{"iter":30,"total":30,"splats":11477,"loss":0.0009310114,"elapsed_ms":6629,"eta_sec":0.0,"stage":"done"}
```

退出码 0。产物:

```
hostrun/out.ply  643,147 字节
ply / format binary_little_endian 1.0 / comment Exported from Brush
element vertex 11477      <- > 0,有效
```

跑法 2 —— 验证 `stop` 控制文件:开一个 5000 步的任务,12 秒后在 control-dir 建 `stop`:

```
{"iter":1170,"total":5000,"splats":11070,"loss":-0.1814408,...,"stage":"exporting"}
{"iter":1170,"total":5000,"splats":11070,"loss":-0.1814408,...,"stage":"done"}
EXIT=0
hostrun/out2.ply  2,614,070 字节  element vertex 11070
```

结论:**训练器代码、JSONL 契约、ply 导出、stop 优雅退出在真实 Vulkan 驱动上全部正常**。
模拟器崩是 gfxstream 驱动的问题,与我们的二进制无关。

### 5.0.1 真 SfM 数据集(native-sfm 产出)在真 GPU 上的验证

`tiny_colmap` 是合成的、text 格式、无 points3D,说服力有限。真正要跑的是
native-sfm 那条 SfM 流水线产出的**二进制** COLMAP:

```
native/testdata/std_dataset_11/
  images/                11 张 640x1136 实拍帧
  sparse/0/cameras.bin   11 cameras
  sparse/0/images.bin    注册 10 张(frame_0025.jpg 未注册,无害)
  sparse/0/points3D.bin  2153 个点
```

注意:这是**二进制** COLMAP,图像名以 NUL 结尾且无长度前缀,**不能**用
`images.txt` 去核对 name(不存在该文件)。

COLMAP 是否被正确消费,看 `RUST_LOG=info` 下这几条判据(本次全部命中):

```
Loading colmap dataset with 10 images
Loaded COLMAP image at path .../images/frame_0000.jpg   (x10,无 "Image not found")
Starting from colmap points 2153
Created init splat from points
Loaded 10 training views
Dataset provides initial splats, using them
```

三组运行,同一份 Windows 原生二进制、真 Vulkan(RTX 5060):

| 步数 | 参数 | 首 loss → 末 loss | 耗时 | splats | PLY |
| --- | --- | --- | --- | --- | --- |
| 30 | res128, sh0 | 0.41518095 (iter5) → 0.40370962 | 2.172 s | 2153 → 2605 | 146,314 B,`element vertex 2605` |
| 500 | res512, sh0 | 0.5441507 (iter5) → -0.075568594 | 5.935 s | 2153 → 2788 | 156,562 B,`element vertex 2788` |
| 2000 | res1024, sh1, refine100, growth1800, export500 | 0.42581472 (iter5) → -0.16757166 | 24.015 s | 2153 → 6033 | 555,686 B,`element vertex 6033`,`comment SH degree: 1` |

2000 步那次的末条 stdout:

```
{"iter":2000,"total":2000,"splats":6033,"loss":-0.16757166,"elapsed_ms":24015,"eta_sec":0.0,"stage":"done"}
```

退出码 0。**留档 PLY:`native/testdata/brush_real_splat.ply`**(555,686 字节,
`element vertex 6033`,SH degree 1)。这份比借来的桌面版 ooosplat PLY 更有说服力,
建议 apk-shell 用它验证查看页渲染。

### 5.0.2 44 帧更大规模基线(ds_44)

native-sfm 提供了 44 帧的 `native/testdata/ds_44/`(1 个 SIMPLE_RADIAL 相机、
44 张注册图、7696 个点、35771 个观测)。同参数、同机器,与 11 帧对照:

| | 11 帧 (std_dataset_11) | 44 帧 (ds_44) |
| --- | --- | --- |
| 初始点 | 2153 | 7696 |
| 末 splats | 6033 | 21680 |
| 首 loss (iter5) | 0.42581472 | 0.27872235 |
| 末 loss (iter2000) | -0.16757166 | -0.15253961 |
| 耗时 | 24.015 s | **68.421 s** |
| PLY | 555,686 B,`element vertex 6033` | **1,995,211 B,`element vertex 21680`** |

2000 步参数:`res1024 / sh1 / max-splats 2000000 / refine100 / growth1800 / export500 / seed42`,
EXIT=0。留档 PLY:`native/testdata/brush_real_splat_44.ply`。

帧数 4×、初始点 3.6×,耗时只涨到 2.85×(sub-linear)。

**显存**(`nvidia-smi -l 1`,88 个样本):空闲基线 1584 MiB(含本机其它占 GPU 的进程),
训练峰值 2488 MiB,**训练净增约 904 MiB**,总显存 8151 MiB。res1024 + 44 帧远没到吃紧。
注意这是桌面独显的数字,手机是统一内存架构,不能直接照搬。

**尺度无漂移**(按 native-sfm 的口径:比质心和最近邻,不看 bbox):

```
44 帧  SfM  (-0.126, -0.474, 4.288)  vs  训练后 (-0.094, -0.437, 4.308)   偏差 0.0522
11 帧  SfM  ( 0.583, -0.684, 3.377)  vs  训练后 ( 0.587, -0.556, 3.338)   偏差 0.1347
```

场景尺度约 4,44 帧质心偏差 0.05 即 **~1.3%**。

最近邻分布(每个 splat 到最近 SfM 点的距离,44 帧):

```
p50 0.0150  p75 0.0342  p90 0.1095  p95 0.2348  p99 0.7306  max 16.0300
< 0.1 : 89.3%    < 0.2 : 94.1%    < 0.5 : 98.0%    < 1.0 : 99.4%
```

最近邻分布说明:98% 的 splat 到最近 SfM 点不到 0.5。max 最近邻 16.03 说明仍有零星
远场 floater,数量级上是几十个点,不影响整体。

**判断尺度用"半径分位倍率"这一张表就够了**(2000 步快照,整体加密 2.82× 作为基准):

| 分位 | SfM | 训练后 | 倍率 | 6000 步时 |
| --- | --- | --- | --- | --- |
| p50 | 0.9440 | 0.9982 | **1.06×** | 1.12× |
| p90 | 1.5002 | 1.7194 | **1.15×** | 1.28× |
| p95 | 1.6659 | 1.9384 | **1.16×** | 1.54× |
| p99 | 2.0318 | 4.1892 | 2.06× | **3.31×** |
| max | 19.1185 | 20.8350 | **1.09×** | **1.39×** |

整体加密 2.82 倍的前提下,**主壳体(p50–p95)只长大 1.06–1.16 倍、最远端只外拓 1.09 倍**。
p99 的 2.06× 如实标出:有一小撮被推到外圈,但包络没有跟着一起走(max 外拓 9%)。这张表不依赖
任何外部参照盒子,也不受分箱影响,与最近邻分布互为印证 —— 是推荐的一句话判据。

> **但这张表只对 2000 步有效(末列是 6000 步的对照)。** 训练加长后每一个分位的倍率都在
> 往上走,p99 从 2.06× 到 3.31×、max 从 1.09× 到 1.39×。详见 5.6.2:包络是**随训练持续
> 外扩**的,不存在一个与步数无关的"分位倍率"常数。

**不要用"落在 SfM bbox 内的百分比"判尺度。** 这个量看起来直观,实际方向是反的:

| | SfM bbox 对角线 | splat 落在其中 |
| --- | --- | --- |
| 11 帧 | 3.068(紧盒子) | **91.3%** |
| 44 帧 | 24.425(被那 52 个离群点撑大 8 倍) | **99.4%** |

参照盒子越松,命中率越高 —— 于是数据越"有问题"反而得分越高。拿它当头条判据会被一眼拆穿。
(此条由 native-sfm 指出,已替换。)

同理,**splat bbox 对角线也不能当尺度指标**:44 帧是 SfM 的 1.7 倍、11 帧是 7.3 倍,
看着吓人,但两者质心和最近邻都正常 —— min/max 会被极少数远场点拉大。

ds_44 本身带远场离群点。训练前后同口径对比(末列补 6000 步,见 5.6.2):

| 指标 | SfM | 训练后 2000 步 | 训练后 6000 步 |
| --- | --- | --- | --- |
| radius > 3 | 52 (0.68%) | 396 (1.83%) | **6,027 (4.39%)** |
| radius > 5 | 15 (0.19%) | 82 (0.38%) | **1,918 (1.40%)** |
| radius max | 19.1185 | 20.8350 | **26.5065** |
| z 上限 | 23.35 | **17.21** | **23.31** |

三条判读:

1. **max 半径只涨 9%**(19.12 → 20.835)—— 但**这是 2000 步的数字**;6000 步是
   26.5065(+39%)。包络随训练持续外扩,不能拿 2000 步当通则。
2. ~~**brush 反而剪掉了最远的 z**(23.35 → 17.21)~~ —— **这条被 6000 步推翻**:
   z 上限回到 23.31,基本等于 SfM 的 23.35。"brush 会裁掉极端远场点"不是稳定行为,
   2000 步那个 17.21 只是当时恰好没有 splat 被推到那么远。
3. 但不粉饰:radius>3 从 0.68% 涨到 1.83%(6000 步 4.39%)。7696 → 21680 是 2.8× 加密,
   按比例应得 ~146,实际 396,**高于线性** —— 弱视差 lane 上确实多长了点。

综合:**traces 有、失控没有**。但"锅主要在 SfM 侧"这句**不成立** —— 5.6.2 的 11 帧
对照(SfM 远场点为 0,6000 步照样长出 586 个远场 splat、其中 393 个远而小)说明训练侧
自己就能持续造出远场尾部,SfM 离群点只是**加了个基数**,不是必要条件。

**数据集来源可信度**:ds_44 的 `images.bin` 曾是对修复前产物做的字节重排版(去掉每条
多写的 8 字节 `name_len`),一度标记为可疑。现已双重证伪:

- 静态:native-sfm 的 `native/sfm/tools/roundtrip_colmap.py` 严格镜像 writer 写出顺序
  重新序列化并逐字节比对,三件套全部 PASS(cameras 64 B / images 1,484,948 B /
  points3D 678,672 B),长度账目反证 1,485,300 − 1,484,948 = 352 = 44 × 8。
- 动态:本节的训练运行是 brush 真正消费它的结果,44 条图像全部解析到、0 条
  `Image not found`、`Starting from colmap points 7696` 精确命中,无半截记录。

即该数据集等价于今天的最终 `libiii_sfm.so` 在同一组输入上的输出。

**统计脚本**:`native/testdata/ds44run/ply_stats.py`(解析 binary PLY,算质心/半径分位/bbox)。
更稳健的一份是 native-sfm 的 `native/sfm/tools/check_ply.py`:按 header 声明的属性类型
累加偏移,不假设属性顺序或固定 stride,并用「文件大小 == header + 顶点数 × stride」自检,
且额外算最近邻分布。本节所有尺度数字都由这两份独立脚本交叉验证过,建议以后用后者:

```bash
python native/sfm/tools/check_ply.py <splat.ply> <colmap_dir>
```

> **半径的原点必须统一,否则两份脚本的数字对不上。** `ply_stats.py` 用 **splat 自身质心**,
> `check_ply.py` 用 **SfM 点云质心**。尾部越重,前者会被自身尾部拖着走,两份产物之间不可比。
> §5.6 全部数字采用后者(SfM 质心)口径。

§5.6 的"位置尾 × 尺度尾"交叉统计用
`native/testdata/steptest/tail_crosstab.py`(SfM 质心口径 + `exp()` 还原尺度),
它能复现本文档已发布的全部尾部数字:

```bash
python native/testdata/steptest/tail_crosstab.py <splat.ply> <colmap_dir> [标签]
```

### 5.1 已验证(模拟器)

x86_64 二进制在 Android 模拟器(API 36.1, x86_64)上:

- 可作为 PIE 可执行文件被 exec
- 挂载 VFS、识别并加载 6 张视图的 COLMAP 数据集(text 格式, `cameras.txt` +
  `images.txt`)
- 初始化 Vulkan adapter:`Goldfish GFXStream (NVIDIA GeForce RTX 5060 Laptop GPU)`
- 输出第一条 JSON `stage":"training"`
- 进入训练循环并推进到 cubecl autotune 阶段

### 5.2 未通过:完整训练跑不出来,原因在模拟器驱动

`out.ply` **没有**生成。tombstone(`/data/tombstones/tombstone_17`):

```
signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0   Cause: null pointer dereference
  #00 vulkan.ranchu.so  std::__1::vector<unsigned long>::push_back(unsigned long const&)+165
  #01 vulkan.ranchu.so  gfxstream::vk::ResourceTracker::on_vkQueueSubmit(...)+4509
  #02 vulkan.ranchu.so  gfxstream_vk_QueueSubmit+2158
  #03 /system/lib64/libvulkan.so  vulkan::driver::QueueSubmit(...)+53
  #04..  /data/local/tmp/libbrushheadless.so
```

排查过的变量(全部崩在同一处):

| 变量 | 取值 | 结果 |
| --- | --- | --- |
| 客户机内存 | MemFree 91MB / 113MB / 232MB | 都崩,且是空指针解引用而非 OOM |
| 输入规模 | `--max-resolution` 32 / 64 / 128 | 都崩(日志里 ReduceCount 恒为 16384) |
| GPU 后端 | `-gpu host` / `-gpu swiftshader_indirect` | 都崩,tombstone 一致(都走 `vulkan.ranchu.so`) |
| cubecl 调优 | `CUBECL_WGPU_MAX_TASKS=1`、`CUBECL_AUTOTUNE_LEVEL=0` | 都崩 |

**结论:(b) 模拟器 gfxstream 驱动问题**。崩溃帧 #00–#03 全在 vendor 的
`/vendor/lib64/hw/vulkan.ranchu.so` 里,我们只是发起了一次普通的 `vkQueueSubmit`。
不是内存不足,不是 brush/cubecl 逻辑错误。真机使用 Adreno/Mali 的真实 Vulkan 驱动,
不受 gfxstream 影响。

**说明:模拟器内的运行验证未通过 —— 只到"启动 + 加载数据集 + 进入训练循环"就被驱动杀掉,
没拿到 ply。** 但同样的二进制在真 GPU 上(见 5.0)跑通并产出了 `element vertex 11477` 的 ply,
所以缺的只是真机(arm64)上的一次实跑。

### 5.3 排查过程中修掉的两个真实缺陷(真机同样受益)

在打补丁之前,模拟器上报 `maxComputeWorkgroupInvocations = 256`,而 cubecl 与 brush
各自写死了更大的 workgroup,wgpu 直接拒绝创建 pipeline:

```
wgpu error: Validation Error
  In Device::create_compute_pipeline, label = 'reduce_kernel_f32_f32_f32'
    Shader entry point's workgroup size [128, 8, 1] (1024 total invocations) must be less or equal to
    the per-dimension limit [256, 256, 64] and the total invocation limit 256
```

已按设备能力钳制(见第 3 节两个文件)。补丁后这条 validation error 消失,剩下的是纯
驱动崩溃。这两处是纯加固:上报 ≥1024 的设备行为不变,上报 ≤256 的设备(部分 Mali)
从必崩变成能跑。

### 5.4 产物级验证:修复确实编进了 .so

改完源码不等于产物里就有。交付前对 `libbrushheadless.so` 做了字符串级双向验证:

```
                        "contains only ply files"  "Dataset provides initial splats"
arm64-v8a/libbrushheadless.so            0                      3
x86_64/libbrushheadless.so               0                      3
```

旧 bail 的报错串消失、新日志串存在,说明 `6060370` 的修复确实进了**两个**产物。文件名、
大小、时间戳都不足以证明这件事(旧包和新包只差 2872 字节),这两个计数可以。真机上只要
stderr 出现 `Dataset provides initial splats, using them`,即可确认跑的是带修复的版本。

### 5.5 目录布局对照实验(澄清一次误判)

apk-shell 在真机/模拟器上拿到
`Error: <path> contains only ply files; a COLMAP dataset directory is required`,
判断是自己把 SfM 的 `--out` 目录(三个 `.bin` 平铺在顶层)直接传给 `--path` 导致的布局问题。
这个判断不成立 —— 用带修复的宿主二进制做了对照:

```
testdata/flat_layout_test/          <- 故意做成平铺布局
  cameras.bin / images.bin / points3D.bin
  images/ (11 张 jpg)

brush_headless.exe --path testdata/flat_layout_test --total-steps 5 \
  --max-resolution 64 --sh-degree 0 --seed 42
```

结果是 **EXIT=0,完全正常**:

```
Loading colmap dataset with 10 images
Loaded COLMAP image at path ... (10 条,无 Image not found)
Starting from colmap points 2153
Created init splat from points
Dataset provides initial splats, using them
Loaded 10 training views
{"iter":5,"total":5,"splats":2153,"loss":0.36363867,"elapsed_ms":1872,...,"stage":"done"}
out.ply 121,002 字节,element vertex 2153
```

结论:平铺布局 brush 能正确消费,**不是**那条报错的成因。

### 5.5.1 四种运行形态对照(给 Android 侧做错误分类用)

同一个带修复的二进制,三种输入的结果完全不同。apk-shell 一度把 B 归因成 A 之外的
第二个 bug,实测后签名是可以明确区分的:

| 输入 | 退出码 | stderr 关键行 |
| --- | --- | --- |
| 平铺 .bin **+ `images/`** | **0** | `Loaded 10 training views` + `Dataset provides initial splats, using them` |
| **部分**图像缺失(44 帧删掉 2 张) | **0** | `Image not found` ×2 → **`Loaded 42 training views`** → 正常训完,`stage":"done"` |
| 平铺 .bin **无 `images/`** | **101** | `WARN ... Image not found: X.jpg` ×N → `Loaded 0 training views` → `panicked at crates/brush-dataset/src/scene_loader.rs:108: Need at least one view in dataset` → `scene_loader.rs:174: Somehow lost data loading channel!` |
| 旧二进制(任意布局) | **1** | `Error: <path> contains only ply files; a COLMAP dataset directory is required` |

要点:

- **判定成败看 `Loaded N training views` 的 N,不看 `Image not found` 的条数。**
  引擎对图像缺失是**优雅降级**的:44 张坏 2 张,就用剩 42 个 view 正常训完(EXIT=0)。
  只有 N=0 才是真失败。所以"出现 ≥2 条 `Image not found` 就拦"会误杀能成功的任务 ——
  正确做法是:N=0 硬拦,N>0 时给降级警告但放行。
- 第三行那串在整个 `native/` 源码树里 grep 已经搜不到(只剩一份 README 和一份旧日志),
  唯一出处就是被 `6060370` 删掉的 `ViewSplats` bail。**看到它 = 跑的是旧二进制**,
  不该归类成"数据集装配问题",否则会误导排查方向。
- 真正的数据集装配失败是倒数第二行:签名是 panic(101)而非 bail(1),必然伴随
  `Loaded 0 training views`。

**已知待改进**:第二行这种"图缺失"目前是 panic + 一堆 tokio worker 同时 panic,
不是优雅报错。理想形态是 JSONL 里给一条 `stage":"failed"` 带 `error` 字段再退出,
这样 Android 侧的错误分类才稳。真机验证后若确认需要再补。

### 5.6 产物的数值特性与两类尾部(消费方必读)

两份留档 PLY 都先做过健全性扫描:

```
brush_real_splat.ply     6033 splats   位置/scale/opacity 非有限值 0
brush_real_splat_44.ply 21680 splats   四元数全部单位化(|q|-1 < 0.01)
文件大小 == header + 顶点数 × stride 自检 OK(stride 92 = 23 属性 × 4)
```

**brush 存的是 log(scale),真实尺度是 exp(scale)。** 消费方算包围盒时必须先 exp,
否则量级完全不对。

#### 5.6.1 2000 步快照:干净输入 vs 带离群点输入

下面这一组是**同一训练长度(2000 步)**下的跨数据集对照 —— 只在这个前提下成立,
见 5.6.2 里 6000 步把它推翻的部分。

11 帧的 SfM 输入是**完全干净**的(radius>3 的点为 0,max 半径仅 1.5142),44 帧的输入
自带 52 个远场离群点(max 19.1185)。两组的训练结果:

| | 11 帧(干净输入) | 44 帧(带离群点输入) |
| --- | --- | --- |
| SfM radius>3 | **0**(max 1.5142) | 52(max 19.1185) |
| 训练后 radius>3 | **39**(max 10.3496) | **396**(max 20.8350) |
| max 半径 | 1.5142 → 10.3496 | 19.1185 → 20.8350 |
| 训练后尺度 exp()>1.0 | 132 | 199 |

**头条判据就用「0 → 39」这个计数** —— SfM 输入远场点为零,训练后长出 39 个。它不依赖
任何基数、任何增长率、任何阈值口径(基线恰好是 0),一个数字就把归因钉死:
远场尾部是训练侧的系统现象,不是从 SfM 继承来的。(6000 步这一格是 0 → 586,同一判据
仍然成立且更强 —— 这是本节**唯一没被 5.6.2 推翻**的结论,因为它的基线恒为 0。)

**不要用 max 半径的增长率当头条。** 6.84×(干净输入)对 1.09×(带离群点)看着像"干净输入
涨得更凶",但那是 1.51 起跳的基数效应:两个起跳点差 12.6 倍,倍数不可比。换绝对增量
(+8.835 对 +1.717)也救不了 —— **倍数和增量都依赖起点,而且两者方向一致**(都是 11 帧涨得
多),不存在谁纠正谁的问题。真正无偏的只有 `0 → 39` 那个计数,它无偏的原因也不是口径更巧,
而是**基线恰好为零**。

**SfM 离群点的作用是加量,包络只小幅外拓**(2000 步口径,6000 步见 5.6.2)。
radius>3 从 39 变成 396(约 10×),同时 max 半径 19.12 → 20.84(**+1.72,外拓 9%**)。
外拓是真的,只是比计数放大慢一个量级 —— 别把"只外拓 9%"写成"包络没动",9% 是实测值,
抹掉它就是把数字往结论上凑。

**尺度侧尾部**(阈值均为 **exp() 之后的世界单位**,整体模型规模比 21680/6033 = 3.59×):

```
阈值(世界单位)   >0.6   >0.8   >1.0   >1.2   >1.5
11 帧 (6033)     229    165    132    111     91
44 帧 (21680)    424    278    199    139     96
倍率            1.85×  1.68×  1.51×  1.25×  1.05×
```

倍率**单调下降到 ~1.05×,且每一档都远低于 3.59×**。所以"大 splat 数量与输入无关"
**只在极端那一档成立**(>1.5: 91 → 96),不能推广成"尺度尾部整体与输入无关" ——
>0.6 和 >1.0 这两档是 1.85× 和 1.51×,它确实在随输入长,只是长得比整体慢。

**但 6000 步下这张表的单调方向整个反过来**(模型规模比 137136/64676 = 2.12×):

```
阈值(世界单位)   >0.6   >0.8   >1.0   >1.2   >1.5
11 帧 (64676)    844    528    375    291    220
44 帧 (137136)  1934   1289    952    776    593
倍率            2.29×  2.44×  2.54×  2.67×  2.70×
```

倍率不再单调下降,而是**略微上升,且每一档都高于 2.12×**。也就是说"极端档反而与输入
无关"这个现象本身是 2000 步的产物 —— 训练拉长后,大 splat 的数量开始跟着输入规模走。
**这张表只能当给定步数下的形态描述,不能当跨步数的规律。**

> **口径警告(我自己在这里错过一次)**:PLY 里 `scale_0/1/2` 存的是 **log(scale)**、
> `opacity` 存的是 **logit**,必须 `exp()` / `sigmoid()` 之后才是世界单位。
> 本表曾误用未经 exp 的 log 值去比世界单位阈值,得出"两帧都是 39"的错误结论 ——
> log>1.0 实际对应世界单位 2.72,不是 1.0。opacity 出现负值(范围约 [-5.0, 7.1])就是
> 它未经 sigmoid 的直接证据。

**按"偏远场 ∧ 偏大"拆成两个群体。** 两个尾部并不独立(44 帧里 199 个大 splat 有 113 个
同时 radius>3)。**注意:这个交叉只是把观测分细,并没有把来源分开** —— 5.6.2 证明两个
群体都主要由训练侧产生:

| 群体 | 11 帧(干净输入) | 44 帧(带离群点) | 倍率 | 当时的归因(已被 5.6.2 修正) |
| --- | --- | --- | --- | --- |
| 远 **且** 大 | 36 | 113 | 3.1× | ~~训练侧,与模型规模(3.59×)大致同步~~ |
| 远 **但** 小 | **3** | **283** | 94× | ~~SfM 侧,跟着那 52 个离群点走~~ |

11 帧里 39 个远场点有 **36 个(92.3%)同时是大 splat** —— 这个比例本身是可靠的观测,
但由此推出的两条归因都**只在 2000 步成立**,见 5.6.2:6000 步的 11 帧(SfM 远场点依然
是 0)长出 393 个"远但小",直接否掉"SfM 侧继承"这条。

推测成因:训练过程在**无任何相机观测到的区域**缺失梯度约束,splat 在那里既不会被
拉回、也不会被剪掉。**这是推测,未做实验验证** —— 要坐实需设计对照实验(如人为遮挡
若干方向再训练比较)。而且 5.6.2 把机制说窄了一层:"无约束**长大**"只描述"远且大"那一
小撮;远场群体的增量主体是**远而小**的(2000→6000 步新增的远场 splat 里,11 帧
390/547 = 71.3%、44 帧 5031/5631 = 89.3% 是小的),
更符合"无约束**漂移/分裂**"而不是"长大"。

#### 5.6.2 训练长度对照:一张 4 点表,推翻了上面两条归因(外加 §5.0.2 的"剪掉最远 z")

`0 → 39` 这类跨数据集对照有个致命缺陷:**两个数据集只跑了同一个训练长度**,所以
"数据集"和"训练长度"这两个变量根本没分开。补两次 6000 步(参数只改 `--total-steps 6000`
与 `--growth-stop-iter 5400`,其余与 5.0.2 完全一致,同机同 GPU):

| | 11 帧 2000 步 | 11 帧 6000 步 | 44 帧 2000 步 | 44 帧 6000 步 |
| --- | --- | --- | --- | --- |
| splats | 6,033 | **64,676**(10.72×) | 21,680 | **137,136**(6.32×) |
| SfM 远场点 | 0 | 0 | 52 | 52 |
| 训练后 radius>3 | 39 | **586**(15.0×) | 396 | **6,027**(15.2×) |
| 远 **且** 大 | 36 | 193 | 113 | 713 |
| 远 **但** 小 | 3 | **393** | 283 | **5,314** |
| 远场中"大"的占比 | 92.3% | **32.9%** | 28.5% | **11.8%** |
| 尺度尾 exp()>1.0 | 132 | 375 | 199 | 952 |
| max 半径 | 10.3496 | **19.9275** | 20.8350 | **26.5065** |
| 耗时 | 24.015 s | 91.268 s | 68.421 s | 110.398 s |

产物:`native/testdata/steptest/ds11_s6000.ply`(5,950,843 B,`element vertex 64676`)、
`ds44_s6000.ply`(12,617,164 B,`element vertex 137136`),均 `EXIT=0`、SH degree 1。
统计脚本 `native/testdata/steptest/tail_crosstab.py` —— 它对本节**全部四个点**以及 5.6.1
里已发布的所有数字都能逐条复现(半径以 SfM 点云质心为原点,与 `check_ply.py` 同口径)。

三条结论,按可靠性排序:

1. **远场群体持续放大,且快于模型规模。** 两个数据集上 radius>3 都是约 **15×**
   (15.0× / 15.2×),而模型规模分别只涨 10.72× 和 6.32× —— 训练越久,远场占比越高
   (11 帧 0.65% → 0.91%,44 帧 1.83% → 4.39%)。**不存在"训到某个规模就定型"**。
   两个 15× 挨得这么近**目前只是两个点,不要当规律用**(而且它比模型规模倍率还整齐,
   恰恰是"点数太少才会出现的漂亮"—— 要坐实得补第三个数据集)。可靠的那半句是
   "远场增长快于模型规模",两个数据集都成立。
2. **远场中"大"的占比单调下降**(92.3% → 32.9%,28.5% → 11.8%),方向在两个数据集上一致。
   即新增的远场 splat 绝大多数是**小**的。这一条同时否掉 5.6.1 的两句归因:
   - "远但小 = SfM 侧继承":11 帧 SfM 远场点恒为 0,6000 步照样长出 393 个 ——
     **训练自己就能造出远而小的 splat**,不需要 SfM 给离群点。
   - "远且大 ∝ 模型规模":44 帧上巧合成立(6.31× vs 6.32×),11 帧上不成立
     (5.36× vs 10.72×,占比直接减半 0.597% → 0.298%)。**两点上看着像规律,第三点就破。**
3. **包络会一直往外走。** max 半径 11 帧 10.35 → 19.93(+93%)、44 帧 20.84 → 26.51(+27%)。
   所以 5.0.2 那个"最远端只外拓 1.09 倍"**是 2000 步的快照,不是训练不变的性质**;
   同理那张「半径分位倍率」表(p99 2.06× 等)也只对 2000 步有效 —— 6000 步时
   44 帧 p99 倍率变成 6.7219 / 2.0318 = **3.31×**。

> **"SfM 离群点不是必要条件"不能被读成"数据集没影响"**(native-sfm 指出,我原稿确实
> 有这个滑坡风险)。固定 6000 步看跨数据集:远场数 11 帧 586 vs 44 帧 6,027,差 **10.3×**,
> 而模型规模只差 2.12× —— **数据集的影响比模型规模大得多**。问题在于这两组数据集在
> "离群点数(0 vs 52)"和"视角数(10 vs 44)"上**同时不同**,现有四个点无法把这两件事拆开。
> 所以"SfM 那 52 个离群点的贡献"目前的正确说法是**未量化**,不是"零"。

#### 5.6.3 第五个点:13000 步(对齐 APK 精档)+ 取景框内的脏点密度

上表只到 6000 步,而 apk-shell 的默认档位是**快档 6000 步 / 精档 13000 步**
(`growthStopIter=10000`)。精档完全在实测之外,补跑一次:`ds_44` @ 13000 步,
`--growth-stop-iter 10000`,其余同前,EXIT=0,耗时 231.5 s。

产物 `native/testdata/steptest/ds44_s13000.ply`(27,630,644 B,`element vertex 300326`)。
第五个点把前面三条结论都压稳了:

| | 11帧/2000 | 44帧/2000 | 11帧/6000 | 44帧/6000 | **44帧/13000** |
| --- | --- | --- | --- | --- | --- |
| splats | 6,033 | 21,680 | 64,676 | 137,136 | **300,326** |
| r>3 总数 | 39 | 396 | 586 | 6,027 | **12,771** |
| 远场中"大"占比 | 92.3% | 28.5% | 32.9% | 11.8% | **9.4%** |
| max 半径 | 10.35 | 20.84 | 19.93 | 26.51 | **28.49** |
| 半径 p99 | 2.278 | 4.189 | 2.834 | 6.722 | **6.359**(略降) |

**包络外扩在减速**:max 半径 20.84 → 26.51(+5.67)→ 28.49(**+1.98**),p99 甚至从
6.722 回落到 6.359。所以"步数越大包络越离谱"**不成立** —— 外扩是收敛的,不是发散的。

**真正决定画面的是取景框内,不是全局。** 按 apk-shell `plyFraming()` 的口径
(水平 `√(dx²+dz²)`、竖直 `|dy|` 的**分轴 p95**,以 SfM 质心为原点)重算五份产物。
脚本 `native/testdata/steptest/framing_stats.py` —— 口径严格对齐
`app/assets/js/viewer.js::plyFraming()`,可直接复核整表:

```bash
python native/testdata/steptest/framing_stats.py <splat.ply> <colmap_dir> [标签]
```

| | 11帧/2000 | 44帧/2000 | 11帧/6000 | 44帧/6000 | **44帧/13000** |
| --- | --- | --- | --- | --- | --- |
| 水平 p95 | 1.075 | 1.677 | 1.253 | 2.050 | **2.115** |
| 竖直 p95 | 1.153 | 1.384 | 1.253 | 1.650 | **1.597** |
| 取景放大倍率 | 5.09× | 2.09× | 2.23× | 2.52× | **2.51×** |

**一条能用的结论:精档的取景框与快档几乎同尺寸**(水平 p95 2.050 → 2.115 只 +3.2%,
竖直 1.650 → 1.597 还略降;取景放大倍率 2.52× → 2.51×)。按 p95 现算取景
**不需要分档位标定**。

> **这一节我曾放进去两条后来被拆掉的"证据",原样留档:**
>
> 1. ~~"取景框内 r>3 飞点恒为 0"~~ —— **构造性结论,不是观测结论**。取景框按分轴
>    p95(≈2.05)定义,"远场"判据是 r > 3,阈值本来就取在取景半径之上,框内当然不可能
>    有 r>3 的点。这个 0 是几何上预定的,与画面脏不脏无关 —— 和本节开头刚批判过的
>    "落在 SfM bbox 内 99.4%"是**同一类自证指标**(参照盒由被测量自己定义)。
> 2. ~~"框内大 splat 占比随步数单调下降(0.125% → 0.078% → 0.019%)"~~ —— **占比下降
>    可能只是分母涨得更快**。绝对数是 27 → **107** → 57:2000→6000 步是**涨 4 倍**,
>    6000→13000 才降。三点不成单调趋势,这条撤回。
>
> 真正能回答"画面脏不脏"的是下面 5.6.4 的视锥内遮挡光栅化 —— 它不依赖取景框,
> 也不依赖任何由被测量自己定义的参照系。

#### 5.6.4 视锥内前景遮挡(与取景无关的独立口径,最终版)

**这一节做过两次,第一次是错的,错法值得记。**

第一次用解析积分 `Σ α·2π√det(cov2d)/屏面积`,结果把**屏外** splat 也按全额计入
—— 一个中心在画面外、只露出一条边的 splat,算出来和摆在正中央一样大。
第二次改成光栅化,但**窗口半径写成了 σ 的倒数**(`sqrt(9·inv)` = 3/σ,想写的
是 3σ),每个 splat 只画出 2px 的核心,前景遮挡被低估约 10 倍,得出
"前景遮挡 2000→6000 涨 3×"的假趋势,还据此否掉了 native-sfm 的建议。
**第三次(本节)才是修好后的数字。**

口径:用 `images.bin` 的训练相机位姿把 splat 投影到每个相机,在降采样 8× 的网格上
按深度分三桶**分别**做 alpha 合成。深度分桶由深度分布定,与取景无关:
主体带 = 有效深度 [p10,p90] 的中位数 d_mid;前景 = d < 0.8·d_mid;背景 = d > 1.25·d_mid。
投影/协方差公式逐条对照 `crates/brush-render/src/shaders/helpers.wgsl`
(156 行 cov3d、111 行四元数顺序、179 行 cov2d),并用 SfM 点重投影自检
(5 个相机中位误差 1.4–1.7 px)。

| | 2000 步 | 6000 步 | 13000 步(精档) |
| --- | --- | --- | --- |
| **前景 occ>0.1 占屏** | **0.271** | **0.269** | **0.271** |
| 前景 occ>0.5 占屏 | 0.113 | 0.157 | 0.160 |
| 主体带 occ>0.1 占屏 | 0.975 | 0.882 | 0.875 |
| 背景 occ>0.1 占屏 | 1.000 | 1.000 | 1.000 |
| 前景/主体 比值 | 0.277 | 0.297 | 0.304 |

三条,这次先说结论强度再说数字:

1. **前景薄雾(α>0.1)不随训练步数增长** —— 三个点 0.271 / 0.269 / 0.271,
   平得不能再平。**"步数越大画面越脏"在这条口径上不成立。**
2. **前景稠密遮挡(α>0.5)从 2000→6000 涨 39%(0.113→0.157),6000→13000 走平
   (+2%)**。增长集中在前段,精档相对快档没有额外代价。
3. **背景与主体带都接近全遮挡(0.875–1.000)** —— 这是**应该的**:照片里的桌面和
   房间本来就不透明,能看穿才说明模型坏了。这一行是本度量的健全性自检。

**结论:不建议动默认步数。** 前景遮挡在 2000/6000/13000 三个步数下基本持平,
精档没有额外脏化代价;而降步数的代价(主体细节下降)是确定的 ——
同帧渲染对比(`native/testdata/steptest/render_2000_frame22.png` vs
`render_13000_frame22.png`,脚本 `render_compare.py`)显示 13000 步的桌面纹理、
笔记本边缘明显比 2000 步清晰。

**这个口径的边界(别再当成"终极裁决")**:前景桶量的是"比主体带近的东西",
里面**混着真实近景几何**(桌面近缘、笔记本近侧),分不出来。所以 27% 这个数
不能直接读成"27% 的画面是雾"。它只能用来做**跨步数的相对比较** —— 而这正好
是本次要回答的问题。真机目测仍然是"到底碍不碍眼"的最终一关,但从裁决降级为验证。

**实践含义(按修正后的口径)**:

- 远场尾部是**训练侧的系统现象**,在任何数据集、任何训练长度上都会出现,不是数据缺陷。
  消费方(查看页取景、下游渲染)按"总会遇到"设计。
- **取景必须读最终 PLY 的分位数,不能套用常数。** splat 的 p99 相对 SfM 点云 p99 的倍率
  随训练长度变化(44 帧 2.06× @2000 步 → 3.31× @6000 步),任何从 SfM 点云推出的固定
  取景系数都会在某个步数上失准。
- 训练步数是**产品参数,不是内部细节**:**但受影响的主要是存储,不是取景**。步数
  2000 → 6000 → 13000 时,PLY 体积 2.0 MB → 12.6 MB → **27.6 MB**(这条必须按实测预算),
  而分轴 p95 取景框基本不动(见 5.6.3)。所以"改默认步数"要重算的是**存储与耗时**,
  取景只要维持"读最终 PLY 的分位数"就不用重标。

**尺度分布**(真实尺度 = exp(log-scale)):

```
              p50      p90      p99     p99.9     max    exp()>1.0   exp()>1.5
11 帧 2000  0.041    0.275    2.077    7.955   33.03    132  2.19%    91  1.51%
44 帧 2000  0.032    0.164    0.951    3.959   23.20    199  0.92%    96  0.44%
11 帧 6000  0.034    0.150    0.708    3.694   25.82    375  0.58%   220  0.34%
44 帧 6000  0.028    0.131    0.764    4.425   31.88    952  0.69%   593  0.43%
44 帧13000  0.022    0.096    0.523    3.909   40.20   1423  0.47%   914  0.30%
```

中位数只占场景尺度(约 4)的 **1%**,主力 splat 正常。撑大包围盒的是尾部那一小撮:
2000 步时 exp()>1.0 占 11 帧 2.19%、44 帧 0.92%,差 2.4 倍。

**训长了之后大 splat 的占比单调下降,两个数据集的分布也收敛** —— 44 帧
0.92% → 0.69% → **0.47%**,p99 从 0.951 → 0.764 → 0.523;11 帧 2.19% → 0.58%。
所以"1%–2% 的尾部决定包络"这个说法只对 2000 步成立,长训练下是 0.3%–0.7%。

注意 `max` 那一列方向相反(23.20 → 31.88 → **40.20**):**极端个体的尺寸仍在变大,
但它们的数量占比在缩小**。这两个趋势不矛盾,合起来的含义是"尾部更瘦更长" ——
对取景无害(见 5.6.3:分位取景框尺寸在 6000 与 13000 之间几乎不变),但对
"按 min/max 算包围盒"的消费方更危险了。

**对取景的影响 —— 这个选择本身就值 2–5 倍:**

"仅用中心"= splat 中心的 bbox 对角线;"中心 ± 半径"= 每个 splat 按 exp(scale) 的最长轴
向三方向各外扩后的 bbox 对角线,即**计入高斯球体积**后的真实包围盒。

| | 仅用中心 | 中心 ± 半径 | 放大 |
| --- | --- | --- | --- |
| 11 帧 2000 步 | 对角线 22.49 | 114.42 | **5.09×** |
| 44 帧 2000 步 | 对角线 41.73 | 87.32 | **2.09×** |
| 11 帧 6000 步 | 对角线 41.04 | 91.69 | **2.23×** |
| 44 帧 6000 步 | 对角线 52.76 | 133.11 | **2.52×** |
| 44 帧 13000 步(精档) | 对角线 57.09 | 143.01 | **2.51×** |

放大倍数在五点之间从 2.09× 到 5.09×,所以"取景系数"不可能是常数,任何硬编码值都会
在某个数据集上崩。**建议用位置的 p99 / p99.5 分位数取景,而不是 min/max** —— 决定包络的
是 0.4%–1.5% 的尾部(极端档 91 与 96 个),分位数对"极少数点"天然免疫,比调系数治本。

> **但分位数不免疫训练长度。** 上面五点里最离群的 5.09× 是「11 帧 + 2000 步」这一格:
> 那时 splat 少、中心 bbox 还只有 22.49,外包络全靠尾部撑。训到 6000 步,中心 bbox 自己
> 涨到 41.04,放大倍率掉到 2.23×,和 44 帧同档(2.52×)。**结论:取景必须读最终 PLY 的
> 实测分位数,不能从 SfM 点云或另一份 PLY 的经验值外推** —— 连"同一个数据集换步数"都
> 会让倍率从 5.09× 变 2.23×。
>
> **反过来看,这也说明 min/max 有多危险:** 44 帧从 6000 步到 13000 步,**分轴 p95 取景框
> 几乎没动**(水平 2.050 → 2.115,竖直 1.650 → 1.597,见 5.6.3),而**中心 bbox 对角线却
> 从 52.76 涨到 57.09**。同一个模型,分位口径说"没变",min/max 口径说"变大了 8%"。
> 取景用分位数还多一个好处:**它不会因为远场飞点变多而把镜头越拉越远。**

#### 5.6.5 查看页视角下基线就是"整屏糊住"——机制、正则扫描与修复(2026-10-15)

这一节是 team-lead 判定的交付阻塞项,也是本项目"画出来看,比算出来自洽,强得多"
的又一次应用:这次**先画图、后下结论**——渲染图在先,扫描表在后,两张图
(`view_base.png` 整屏白雾 vs `view_1e5_aux10.png` 笔记本可辨)比下面所有数字加起来
更有说服力。

**先认账:5.6.4 的口径选错了相机。** 5.6.4 用训练相机回答"跨步数相对比较"没问题,
但产品问题("用户在查看页会不会看到雾")的相机是**查看页合成相机**
(yaw30/pitch18、dist≈10–12、竖屏 asp≈0.4497)。从这个相机看,基线(默认参数)
的画面是这样的(`tools/viewrender.py`,渲染公式与 5.6.4 同一套、已对训练视角验证过):

| 渲染图(查看页视角,44 帧 6000 步) | 观感 |
| --- | --- |
| `D:/brushsweep/view_base.png`(默认参数) | **整屏白雾,主体勉强可辨** —— 确认 team-lead/native-sfm 的反转 |
| `view_1e-6.png`(scale 正则 1e-6) | 主体清晰,中部仍有雾团 |
| `view_1e-5.png`(1e-5) | 主体清晰,键盘可读,残留少量雾 |
| `view_1e5_aux10.png`(1e-5 + aux 到满时长) | 最干净,但有针状条纹伪影 |

**机制:巨型片为什么能活到训练结束**(team-lead 定位,`brush-train/src/train.rs:356-379`):
prune 的三个条件是 `raw_opacity < inv_sigmoid(MIN_OPACITY)`(只删太透明)、
`log_scales < -15.0`(只删太小)、`|means−center| > median_size×10`(只删太远)。
一个 σ=10.9、α=0.74、r=8.6 的片**三个都不命中**,一路活到导出。这是机制空档,
不是玄学。

**两个正则的真实语义**(读源码读出来的,`train.rs:143-204`):

- `scale_loss = Σ exp(scale)·vis / median_size` —— 对 log_scale 的梯度 ∝ **自身尺度**,
  所以它天然选择性:σ=31 的片受力是 σ=0.03 片的一千倍。这正是"压尾部不伤主体"的
  形状,后面 p50 实测也证实了。
- `aux_loss_time` **不只是时长,还是幅度**:`weight = (aux_loss_time − train_t).clamp(0,1)`。
  默认 0.9 = 峰值幅度只有配置值的 0.9 倍,且最后 10% 步数完全没正则压着。

**CLI 缺口(必须告诉 apk-shell 的一句)**:`HeadlessArgs` 原来根本没透传这三个参数
(见 §4.2 的新增说明),所以"改一个 CLI 参数、Java 侧加一行、两个 ABI 不用重编译"
这条路**原本不存在**。本次已加 `--opac-loss-weight / --scale-loss-weight / --aux-loss-time`
并重建 host 二进制;**Android 两个 ABI 的 .so 都要重编才能吃到**。旧 .so 收到新参数
会因 clap 未知参数直接退出——apk-shell 侧要做版本判别。

**扫描实验**:ds_44、6000 步(快档实际档位)、其余参数全按 `paramsFor()` 默认。
雾团指数 = native-sfm 口径(`occlusion_stats.py` 初始 yaw30,典型=三轴几何平均),
拆分 = `tools/fog_split.py`(到最近 SfM 点距离 < R 判为有支撑):

| scale_loss_weight | splats | exp(scale) p50 | max σ | 最终 loss | 雾团指数 典型/上界 | 雾里无支撑占比 |
| --- | --- | --- | --- | --- | --- | --- |
| 1e-8(基线) | 137,136 | 0.0279 | 31.88 | −0.1660 | 0.499 / 1.604 | 96–100% |
| 1e-6 | 40,585 | 0.0330 | 17.31 | −0.1664 | 0.147 / 0.490 | — |
| **1e-5** | **35,653** | **0.0253** | **13.24** | **−0.1666** | **0.072 / 1.269** | — |
| 1e-4 | 21,704 | 0.0110 | 8.62 | −0.1512 | 0.050 / 0.692 | — |
| 1e-3 | 14,716 | 0.0000 | 4.41 | −0.1281 | 0.0004 / 0.279 | — |
| **1e-5 + aux_loss_time=1.0** | **33,908** | **0.0226** | **6.75** | **−0.1657** | **0.047 / 0.626** | — |
| 精档 13000 基线 | 300,326 | 0.0220 | 40.20 | −0.1740 | 0.535 / 1.094 | 96–100% |
| **精档 13000 + 同参数** | **60,621** | **0.0144** | **4.65** | **−0.1828** | **0.011 / 0.583** | — |

三条读数:

1. **scale 正则有效且选择性如预期**:1e-5 把雾团指数从 0.499 压到 0.072(过了 0.1
   锚点),p50 只动 −9%,loss 反而持平略好。1e-4 开始 p50 塌(−61%),1e-3 全灭
   (p50=0,loss −0.128)。
2. **aux_loss_time=1.0 一并要改**:max σ 从 13.2 再砍到 6.75(基线是 31.9),雾团指数
   再降 35%,loss 仍持平。证实"最后 10% 无正则 → 尾部反弹"是真的。
3. **`opac_loss_weight` 是错误的旋钮,不要碰**:1e-6 就把 splats 打到 17,664、loss 掉到
   −0.142,而雾团指数反而升到 0.62(它杀掉的是小而透明的片,活下来的大片占比更高,
   p50 从 0.028 涨到 0.064)。维持默认 1e-9。
4. **精档同样吃这套参数,收益更大**(补测,消除"精档未实测"缺口):13000 步 + 同参数
   雾团指数 0.535 → **0.011**(降 98%),max σ 40.2 → 4.65,loss 反而更好(−0.183 对
   −0.174),PLY 27.6 → **14.3 MB**。p50 降得多一些(−35%),渲染
   (`D:/brushsweep/view_fine_reg.png`)主体清晰、键盘可读,残留针状条纹。
   **所以快档/精档可以共用同一组正则参数,不需要分档标定。**

**雾的成分:是离散 floater,不是统计性薄雾**(`fog_split.py`,换 R 不翻:
R1×{1,2,4} 与 d_nn p50/p90/p95 六种半径下结论一致):查看页"主体前"的雾贡献
96–100% 来自**到最近 SfM 点距离很远**的 splat —— 有 SfM 支撑的真实近景几乎不贡献。
所以"删掉几个 floater 就能治好"在成分判断上是对的,但 team-lead 也验证过只删巨型片
治不了(11帧/6000 巨型片 0 个指数仍有 1.1),正则让这一整类东西长不出来,才是治本。

**顺带澄清两个"差 6 倍"的数**(team-lead 问"该信哪个"):我的 0.269 与 native-sfm 的
0.499 **不是同一个泛函,也不是同一个相机**。0.269 是"α 累计 >0.1 的屏幕面积占比"
(训练相机,dist≈2,前景桶里混着桌子近缘等真实近景);0.499 是"Σ α×占屏比"
(查看页相机,dist≈12,"主体前"是一片离主体 2 个单位以上的空腔)。换到同一个泛函
(屏幕平均累计 α)我的数是 0.135/0.160/0.162(2000/6000/13000),与他的差 3 倍且
差距方向可解释:**他的相机前那片空腔里几乎只有 floater(fog_split 证实),我的
训练相机"前景"里主要是真几何**。所以回答产品问题该用他的口径,我的口径用于
跨步数对照——两个都保留,但别再互相当对方的上界。

**推荐值:`--scale-loss-weight 1e-5 --aux-loss-time 1.0`**,`opac_loss_weight` 不动。
代价与残余风险:

- splats 137k → 34k(−75%),远超 team-lead 定的 30% 红线。我仍推荐,理由:loss 持平、
  p50 只降 9–19%、训练视角渲染(`D:/brushsweep/trainpose_1e-5.png`)主体无空洞、
  鼠标/屏幕边缘反而比基线清晰——**少掉的 10 万个 splat 是重复铺在已覆盖区域的冗余,
  不是主体**。但这条判断最终要真机真渲染器确认(我的渲染器无高阶 SH、无 tile 排序)。
- 残余雾仍有 0.047,且上界口径(1.27)说明还有 1–2 个极端各向异性片,特定转角下
  可见(绕圈最坏 0.62)。彻底清零需要训练后 prune,现阶段不值得。
- PLY 体积 12.6 MB → **8.4 MB**,对 apk-shell 的查看页内存峰值是直接利好。

> **⚠ 上表作废警告(2026-10-15 补)**:本节第一张表的"基线 137,136"和各组 PLY 都是
> **二进制默认参数**(res1920 / sh3 / max-splats 5M / refine200 / growth15000)跑的,
> **不是 APK 的生产参数**。`paramsFor()` 快档实际是 res960 / sh1 / max-splats 1.5M /
> refine100 / growth4000,精档是 res1280 / sh3 / 4M / refine200 / growth10000。
> 最要命的是 `refine_every` 差一倍(100 vs 200),密集化是乘法过程,6000 步下来
> splat 数差 3.3×。所以"基线雾团 0.499"和"0.499 → 0.047 降一个数量级"这两句
> **都撤回**。这张表只保留一个用途:**扫描内部只改 scale 正则一个变量,趋势单调可信**
> (雾 0.137 → 0.070 → 0.050 → 0.0004;p50、loss、splats 同向单调)。
> 跑间方差实测只有 ±1%(同参同 seed 三次:41,353 / 40,893 / 41,082;换 seed 7:41,082),
> 排除了随机性。native-sfm 的混杂怀疑成立,但根因不是他猜的"新旧二进制差异"。

#### 5.6.6 按生产参数(paramsFor)重扫:最终推荐(2026-10-15)

快档:`res960 / sh1 / 1.5M / refine100 / growth4000 / 6000步 / seed42`;
精档:`res1280 / sh3 / 4M / refine200 / growth10000 / 13000步 / seed42`。
雾团指数 = native-sfm 口径(饱和/典型,竖屏 asp0.45),三行 = 初始 yaw30 / 绕 72 朝向最坏 / 绕圈中位。
loss = 末 200–300 轮平均值(末值单点噪声大,不用)。

**快档:**

| scale_loss_weight | splats | Δsplats | p50 | Δp50 | maxσ | loss | Δloss | 雾 初始 | 雾 最坏 | 雾 中位 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 无(基线) | 80,948 | — | 0.0315 | — | 50.1 | −0.1720 | — | 0.266 | **0.984** | 0.0168 |
| 1e-6(两次) | 76,777 | −5% | 0.0267 | −15% | 11.5–18.0 | −0.1714 | −0.4% | 0.127 / 0.059 | 0.089 / **0.929** | 0.0044 / 0.0139 |
| 3e-6 | 72,000 | −11% | 0.0216 | −31% | 12.3 | −0.1694 | −1.5% | 0.309 | 0.312 | — |
| **1e-5+aux1.0(两次)** | **60,611** | **−25%** | **0.0154** | **−51%** | **4.3–10.8** | **−0.1680** | **−2.4%** | 0.018 / 0.193 | **0.039 / 0.051** | 0.0082 / 0.0034 |

**精档:**

| | splats | p50 | maxσ | loss | PLY | 雾 初始 | 最坏 | 中位 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 无(基线) | 89,742 | 0.0281 | 38.8 | −0.1737 | **21.2 MB** | 0.214 | 0.459 | 0.0236 |
| 1e-5+aux1.0 | 48,216 | 0.0147 | 9.8 | −0.1680 | **11.4 MB** | 0.038 | **0.018** | 0.0036 |

三条读数:

1. **基线在生产参数下也糊**:快档绕圈最坏 0.984(整屏),精档 0.459;**正则后两个
   档的最坏都压到 0.02–0.05**,过 team-lead 的"中位 ≤0.1 且最坏 ≤0.3"双判据。
2. **雾团指数单次跑观测到 10× 级差异**(措辞要准:这是 **n=2 的观测极差,不是方差
   估计**,不能写成"方差 10× 级"——两个样本的极差本身波动极大,要断言方差需 n≥3),
   **但归因不是"单个极端个体"——这一条我写错过一次,在此留档**。观测:1e-6 两次的绕圈最坏 0.089 / **0.929**;1e-5 两次的
   初始 0.018 / 0.193。我最初归因成"漏放一个 σ=18 的巨型片"(看着像 #10522 那种
   一人贡献 89% 的情形),**但量化后站不住**:按上界口径算最严重单个 splat 的贡献占比,
   六个生产档产物是 **17.3% / 19.1% / 19.6% / 27.2% / 29.7% / 18.3%** —— 没有谁被
   一个片主导。方差来自**中等飞点的集体尾部**(几十上百个 splat 的存废),不是一个人。
   后果很重要:**"追杀最大的那几个片"这条思路在生产参数下不成立**(它只在默认参数
   那批、#10522 一人 89% 时才成立),这与 team-lead 已撤回的"巨型片计数"是同一类
   伪指标。**正确做法是复跑取中位数 / 看单调量,不要按个体去删。**
   **⚠ 但这句话要带格位**(5.6.7 补):上面的 17–30% 测于各产物自己的初始/最坏格;
   把格位钉死到 yaw 0° 后,典型口径 top-1 是 **55.2% / 66.6% / 12.5%**,上界口径
   30.9% / 70.8% / 30.2% —— 基线和 reg① 在该格位就是被 1–2 个片主导(top-3 典型
   已达 80.9% / 74.7%)。所以准确说法是**"在 5.6.6 这批格位下不成立"**,不是普遍
   结论。**集中度(top-1 占比)和水平一样是格位相关的量**,抽掉格位都不能说。
   选 1e-5 不选 1e-6 的实际依据也相应修正:不是"1e-6 有概率漏一个巨型片",而是
   **1e-5 的两次复跑最坏值都低(0.039/0.051),1e-6 两次里有一次是 0.929** —— 用
   "复跑后稳不稳"这个可复现的判据,不用机制猜测。
3. **单调、可复现的量是 splats / p50 / loss**:1e-6→3e-6→1e-5 三档全部单调
   (splats −5/−11/−25%,p50 −15/−31/−51%,loss −0.4/−1.5/−2.4%)。1e-5 的 p50 −51%
   是真实代价,训练视角渲染(`D:/brushsweep/trainpose_prod_reg.png`)主体无空洞、
   鼠标/桌面/屏幕边缘都在。**"主体保护判据 = p50 降幅 + loss 持平"(team-lead 改判
   后的红线)在这里就是:p50 砍半换雾团从 0.98 → 0.04,这笔交换值得,由真机最终裁决。**

**最终推荐(生产口径):`--scale-loss-weight 1e-5 --aux-loss-time 1.0`,快/精档同传,
opac 不动。** 附带更正:生产精档基线是 **89,742 splats / maxσ 38.8**,不是旧文档里的
300,326 —— 那个数同样是错误参数下的产物。生产精档加正则后 PLY 约 **11 MB**(48,216
splats),不是 27.6 MB。

> **三个量量纲不同,真机必须分三项独立判,不能合并**(native-sfm 提的口径边界):
> - **雾团指数** = 屏幕覆盖率(α×占屏比)→ 只回答"糊不糊";
> - **`exp(scale)` 的 p50** = 尺寸 → 只回答"splat 变小了没有";
> - **清晰度** = **两者都不衡量**,p50 下降既可能是变清晰也可能是主体被削没。
>
> 所以 1e-5 档的"雾压到 0.039"**不能**用来回答"主体清不清楚"。我的渲染器无高阶 SH、
> 无 tile 排序,只能看几何大概,**清晰度只能真机裁决**。真机若发现主体明显变稀疏,
> 才启用回退档 `1e-6`,**且回退不是免费的**,代价见下。
>
> **回退档 1e-6 的生产口径实测(两次复跑,native-sfm 复核一致)**:
>
> | 1e-6 | 初始 yaw30 | 绕圈最坏 | 绕圈中位 |
> | --- | --- | --- | --- |
> | ① 典型 | 0.127 | 0.089 | 0.004 |
> | ① 上界 | 0.503 | 1.150 | 0.086 |
> | ② 典型 | 0.059 | **0.929** | 0.014 |
> | ② 上界 | 0.751 | **2.512** | 0.117 |
>
> 对照基线(无正则):初始 典型 0.266 / 上界 0.773;**绕圈最坏 典型 0.984 / 上界 1.705**。
> 所以 1e-6 的收益只有 p50(−52% → −15%)、loss(−2.4% → −0.4%)、splats(−25% → −5%),
> 而**最坏视角的雾没有改善:上界口径下反而更差(2.51 vs 1.70),典型口径下与基线相当
> (0.93 vs 0.98)**。这是个明确的取舍,不是免费回退。
>
> **再加一条:回退档的雾在最坏格上两次跑相差 10×(典型 0.089 / 0.929)**
> (同样只是 n=2 的观测极差,不是方差估计)。1e-5 两次跑在最坏格上 0.039 / 0.051。
> **但真正排除 1e-6 的硬理由不是"倍差大",而是"观测值跨过了判据线"**:
> 判据线取绕圈最坏 ≤0.3 时,1e-5 两次都在线下(0.039 / 0.051),1e-6 一次在线下
> (0.089)、一次在线上(0.929)。**是否跨线是二值判断,n=2 就够**,不需要方差估计。
>
> **判据线自己是自由参数,必须扫**(第六类坑),native-sfm 扫完的结果是
> **判决在整个 `(0.051, 0.929)` 区间内不变**(区间宽 18×):
>
> | 线 | 1e-5① 0.039 | 1e-5② 0.051 | 1e-6① 0.089 | 1e-6② 0.929 | 无正则 0.984 |
> | --- | --- | --- | --- | --- | --- |
> | 0.1 | 线下 2.55× | 线下 1.96× | 线下 1.13× | **过线 9.29×** | 过线 9.84× |
> | 0.3 | 线下 7.65× | 线下 5.87× | 线下 3.38× | **过线 3.10×** | 过线 3.28× |
> | 1.0 | 线下 25.5× | 线下 19.6× | 线下 11.3× | 线下 1.08× | 线下 1.02× |
>
> 线的可用区间由两端夹出来:线 ≤0.051 时 1e-5 自己过线、判决翻转;线 ≥0.929 时
> 1e-6② 不再过线,同时无正则基线也判合格——**与"基线是已知明显档"的锚点语义矛盾**
> (1.0 = 满屏不透明),所以线必须 < 0.984。上界不是拍的,是锚点语义给的。
>
> **⚠ 我写错过的一句,在此留档**:我原写"1e-5 离线 6×,即使再来 10× 波动也不会跨"——
> **算错了**:实际余量 0.3/0.051 = **5.87× < 10×**,而我们在同批数据里已经实测到
> 10.46× / 10.68× 的倍差,**余量小于已观测到的波动幅度**,根本不是安全余量。
> 更根本的错误是:**用"余量足够"绕开方差问题只是把 n=2 的断言藏进了一个未经检验的
> 前提**,没有真的绕开样本量限制。**结论的稳健性来自"区间内判决不变"这个可证的
> 事实,不来自"余量足够大"。** 已删掉余量表述,改用区间表述。
>
> **另一个 n=1 别漏**:无正则基线的绕圈最坏只跑了 **1 次**(0.984),所以"基线过线"
> 是单次观测——用来**校验线的位置**(线必须 <0.984)够用,但**不能**用来估计基线
> 自身波动,也不能说"基线必然过线"。
>
> **但"稳定"是格位相关的,别整体上断言**(这一条防止反向过读):1e-5 在**最坏格**
> 稳定(0.039/0.051),在**初始格**其实也不稳(0.018 / 0.193,同样差 10×);
> 1e-6 反过来(初始格 0.127/0.059 差 2×,最坏格差 10×)。所以规则是
> **先固定格位(哪个视角、哪种各向异性口径),再看该格位的复现性**,
> 不能说"某档稳定/不稳定"这种整体判断。决策用的是最坏格,所以看最坏格。
>
> **⚠ 又犯了一次同类错(留档)**:我此前两处写的"回退档雾团 0.147、p50 +18%"
> (本节原句 + apk-shell `PipelineManager.java` 的字段注释)**都来自默认参数批次**,
> 是刚定义完的"两个不同总体的同名指标"错误的再犯。生产口径下 p50 是 **−15%**(不是
> +18%),雾团不存在单一的 0.147 这个值。已通知 apk-shell 修掉那条代码注释。
>
> 另外,`top-1 贡献占比`(本次 17–30%)只用于否定"按个体删/抑制最大的几个片",
> **不能反过来推导正向操作** —— 它回答"是被一个还是一群撑起来的",不回答"该不该删"。
>
> **为什么雾团指数不稳、而取景指标很稳**(apk-shell 实测:取景入画率 run-to-run
> ≤0.3pp、占宽 ≤6.6pp,而雾团指数两次差 10×):取景用的是**位置分位数**(分轴 p95),
> 分位数对少数极端个体天然免疫,换掉几十个飞点几乎不动 p95;雾团指数是**尾部积分量**
> (Σ α×占屏比),直接对"这批中等飞点在不在"敏感。两者都没算错,量的东西不同。
> 这也从另一头支持"取景用分位数、不用 min/max 或固定系数"。
>
> 最后一条术语:本节的"参数集外推"更准确的说法是(native-sfm)**"两个不同总体的
> 同名指标"** —— 换 k 至少还在同一批数据内,参数集换了连总体都不同,连"同一指标的
> 两个取值"都算不上。

#### 5.6.7 子群 F(近相机最近 30%)是否必要:四路对比(2026-10-15)

**问题**:apk-shell 在查看页取景里提出诊断子群 **F = 到相机最近的 30%**。问的是:
既有结论里有没有哪条其实**依赖**这个子群口径。依赖 → 结论是被口径造出来的;
不依赖 → F 只留作诊断,不进决策链。

**格位固定**(不在四路之间换格,换格比较是本项目栽过的坑):
竖屏 asp 0.4497 / pitch 18° / **yaw 0°**。
先纠一个前提:**两次复跑的"最坏角"并不都在 0°/10°** —— 实测
`prod_fast_reg` 的最坏是 yaw 0°,`prod_1e5_r2` 的最坏是 **yaw 340°**,
无正则基线的最坏是 **yaw 15°**(不是 0°)。所以"最坏格"这个词本身是
**逐产物**定的;本节统一钉在 yaw 0°,三个产物在该格位的雾团是
典型 0.665 / 0.039 / 0.019,上界 1.185 / 1.272 / 0.794。

**先说一个退化陷阱**:F 的母体如果取"视锥内全量",**F 会退化成恒等式** ——
`front`(主体前)那一批本来就在最前面,最近的 10% 就已经 100% 把它包进去了
(实测 F=10% 时"雾占比"已经是 100.0%,`F=50%` 还是 100.0%)。这一路的
"F 占 100%"没有任何判别力。**所以 F 只能定义在 `front` 母体上**,本节所有数都是这个口径。

**四路主表**(母体 = front,F = 其中到相机最近的 30%)。该格位 `front` 的规模:
基线 517 个(视锥内 79,561)、reg① 160 个(59,103)、reg② 173 个(59,743)——
F=30% 只有 48–155 个 splat,所以这四路的雾都是**小样本尾部积分量**,本身波动就大:

| 路 | p50(σ_max) | 雾 典型 | 雾 上界 | n |
| --- | --- | --- | --- | --- |
| F · 正则前 | 0.3221 | 0.6136 | 0.9709 | 1 |
| not-F · 正则前 | 0.1949 | 0.0513 | 0.2144 | 1 |
| F · 正则后① | 0.1009 | 0.0317 | 1.1687 | 2 |
| not-F · 正则后① | 0.0921 | 0.0073 | 0.1032 | 2 |
| F · 正则后② | 0.2072 | 0.0120 | 0.6356 | 2 |
| not-F · 正则后② | 0.0984 | 0.0067 | 0.1583 | 2 |

正则前→后 的方向:

| 栏 | F 路径 | not-F 路径 | 同侧? |
| --- | --- | --- | --- |
| p50 | −69% / −36% | −53% / −49% | **同侧**(都降) |
| 雾 典型 | −95% / −98% | −86% / −87% | **同侧**(都降) |
| 雾 上界 | **+20%** / −35% | −52% / −26% | **① 反号** |

**结论:典型口径(决策口径)与 p50 两栏四路同侧 → F 可弃。**
上界口径有一次反号,但**它不是 F 造出来的**:全量(不切子群)上界在同一格位也是
1.185 → 1.272(+7%) / 0.794(−33%),同样是 ① 升 ② 降。也就是说
**上界口径在 yaw 0° 这个格位下,全量自己就不给出一致方向**,切不切 F 都一样分裂。
反过来说,**F 不是"能救回一致性"的口径**:把 F 剥掉看 not-F,上界两次都降,
看起来一致,但那是把一个"离相机更远的飞点子集"拿来当替身,换成回答"用户在查看页
看到什么"就错了 —— 用户看到的是全量。

**口径选择规则**(team-lead 要的"什么时候看 F、什么时候看整体"):

1. **决策(选档 / 判过线)只看:典型口径 + 最坏格 + 全量 front。** 理由:判据线
   0.1 / 0.3 是在典型口径下标定的(5.6.6 的扫线表全用典型),换口径等于换判据线,
   不可比。F 不进这条链。
2. **上界口径只有一个用途:判断"有没有针状极端个体"**。它的物理含义是"最不利朝向",
   不是期望值。实测 reg① 在 yaw 0° 有 **1 个** σ_max∈[1,5) 的针状片自己占上界雾的
   **70.8%**(0.901/1.272)—— 这种被单个体撑起来的数不适合跨配置比大小,
   更不适合判过线。
3. **什么时候该看 F**:只在要做**空间局部处置**时看(距离裁剪、近场 prune、
   判断"要不要限制查看页最近距离")。此时 F 的"占 64–92%"是**必要条件**:
   它说明处置能打到大部分雾。
4. **什么时候不看 F**:做**全局处置**(调正则、换步数、换档位)时不看,看全量即可。
5. **冲突时的仲裁**:F 的结论与全量不同向时,**以全量为准**,并把不同向记录在本节
   (不删、不改写)。

**两条历史判断的检验结果**:

- **J1「雾团主要来自近相机侧」—— 成立,且换参数不翻。**
  F 占雾的比例:f=20% 时 90.7 / 73.7 / 58.0%;f=30% 时 **92.3 / 81.2 / 64.3%**;
  f=50% 时 93.5 / 87.0 / 70.2%(典型口径);上界口径同区间 67.6–94.7%。
  **两个口径、f∈[0.2,0.5] 全区间都 >50%**,apk-shell 定义的 30% 落在稳定段内。
  一个边界:**f=10% 时典型口径 reg② 掉到 36.0%(<50%)**,上界仍是 64.9%。
  所以"最近的三成"稳,"最近的一成"不稳 —— 结论对 f 不敏感,但只在 f≥0.2 时。
  相机距离混杂也按住测过:强制三批共用 dist=11.943(基线自己的取景距离),
  F 典型占比 92.3 / 75.3 / 77.0%,仍全部 >50%。
- **J1 的后半句「中等飞片,不是少数几个巨型片」—— 在这个格位不成立,撤回。**
  yaw 0° 的 top-1 占雾比例:典型 **55.2% / 66.6% / 12.5%**,上界 30.9% / 70.8% / 30.2%。
  基线和 reg① 在这个格位**就是被 1–2 个片主导**(典型口径 top-3 已达 80.9% / 74.7%)。
  这与 5.6.6 记的"top-1 17–30%、没有个体主导"**不是矛盾,是格位不同**:那批数测于
  各产物自己的初始/最坏格,本节钉死在 yaw 0°。
  **又一次印证"稳定是格位的属性":top-1 占比也是格位相关的量,不能抽掉格位说
  "有没有个体主导"。** 5.6.6 里"追杀最大的几个片不成立"那句,按本节的数要降级为
  **"在 5.6.6 那批格位下不成立"**,不是普遍结论。

- **J2 换成我实际在档的那一条。** team-lead 记的 J2 是"正则削小尺度的副作用主要落在
  近相机子群",**这不是我在档的结论**;5.6.5 在档的是
  **「scale_loss 对 log_scale 的梯度 ∝ 自身尺度,所以选择性是"按尺度"的,不是"按距离"的」**。
  实测这一条也不是"按尺度"——**p50 降幅在两个轴上都是近似均匀的**:

  | 切分轴 | 组内 p50 前→后 | 补集 p50 前→后 | 比值 |
  | --- | --- | --- | --- |
  | 距离轴 F=30%(母体=视锥内全量) | 0.0427 → 0.0181(−58%) | 0.0267 → 0.0139(−48%) | 1.20 |
  | 尺度轴 L=30%(最大的三成) | 0.1078 → 0.0536(−50%) | 0.0200 → 0.0098(−51%) | 0.99 |

  分布整体比值(reg 两次的均值 / 基线):p50 0.487、p90 0.493、p99 0.442、**max 0.15**。
  **所以准确的表述是:正则把整个 σ 分布等比压了约一半(p50/p90/p99 的比值都在
  0.44–0.49),额外收益只出现在极尾(max σ 50.1 → 4.3–10.8)。**
  "梯度 ∝ 自身尺度 → 天然选择性压大 splat"那句**必须降级为未经实测验证的机制描述**:
  它预测大 splat 的相对收缩应远大于小 splat(比值该远小于 1),实测是 0.99。
  (保留一点余地:p50-of-halves 不是梯度干净的实验——splat 总数从 80,948 降到
  60,338/60,884,密集化与 prune 同时改写组成,所以机制那句没有被**否证**,
  只是**没有被这个观测量支持**。)

**n 的标注**(这一节每个量的样本量):
- 正则前三行:**n=1**(只有 `prod_fast_base.ply` 一次训练),且该产物在 yaw 0° 只有
  一个观测 → 只能用来**确定"正则前"这一侧的位置**,不能用来谈它自不自然稳。
- 正则后六行:**n=2**(`prod_fast_reg` / `prod_1e5_r2` 两次独立训练,同 seed)。
- 尺度分桶、top-k、固定相机复算:同上(n=1 / n=2)。
- **因此本节不给出任何"波动幅度"的断言**,只给"方向是否一致"这个二值判断。


## 6. 真机验证命令(待执行)

```bash
adb push prebuilt/arm64-v8a/libbrushheadless.so /data/local/tmp/
adb push <colmap数据集目录> /data/local/tmp/ds
adb shell "cd /data/local/tmp && chmod 755 libbrushheadless.so && RUST_LOG=info \
  ./libbrushheadless.so --path /data/local/tmp/ds --export-path /data/local/tmp/out.ply \
  --total-steps 300 --max-resolution 1024 --export-every 100 --seed 42"
adb shell "head -c 200 /data/local/tmp/out.ply"   # 确认 element vertex 行数 > 0
```

判据:stdout 全程有 JSONL,最后一条 `stage":"done"` 且 `iter` 接近 `total`;
`out.ply` 的 PLY header 里 `element vertex N` 的 N > 0。
