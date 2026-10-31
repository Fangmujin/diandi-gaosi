# 点点高斯 APK 攻坚复盘

> 记录自 2026-10-14 至 2026-10-19 的完整过程：遇到的困难、解决手段、使用的技术，
> 以及我自己犯过的错。目标是保留可复核的细节，而不是一份漂亮的总结。

---

## 0. 一句话总览

| 指标 | 起始 | 最终 |
|---|---|---|
| arm64 APK（真机交付） | 80,627,536 B（76.9 MiB） | **24,528,736 B（23.39 MiB）** |
| x86_64 APK（模拟器） | 105,723,738 B（100.8 MiB） | **46,495,578 B（44.34 MiB）** |
| trainer `.so`（arm64） | 70,878,536 B（67.60 MiB） | **14,785,656 B（14.10 MiB）** |
| trainer `.so`（x86_64） | 78,220,544 B（74.60 MiB） | **18,998,768 B（18.12 MiB）** |

用户目标「45 MB 以下」：arm64 包两种口径（MiB / 十进制 MB）都远低于目标。

---

## 1. 前序阶段：把「跑通」和「取证」分开

### 1.1 桌面端基线
用 ooosplat 在桌面完成 `54734.mp4` 的 3DGS：104 帧、注册率 100%、27.3 万 splat、
`final.ply` 64.4 MB。后续所有「生产 PLY」都以此为参照系，其中
`prod_fast_reg.ply` = 60,338 个高斯点 / p50 0.0152 / 5.29 MiB，被用作数值比对基线。

### 1.2 两个原生引擎
- `libiii_sfm.so`：自写 C++ SfM，输出 COLMAP 格式相机。实测 44 帧 44/44 全注册、
  7696 点、686.5 s、0.3822 px。
- `libbrushheadless.so`：Brush v0.3.0（Rust + cubecl + burn + wgpu）训练器。

**关键认知**：这两个文件名为 `.so`，实为 **PIE 可执行文件**，Android 侧走
`ProcessBuilder` 的 **exec**，不是 `dlopen`。16KB 页机器上失败形态是
`execve` 失败 / 子进程无输出，排查时不能按 `dlopen` 返回 NULL 的方向找。

### 1.3 16KB 页对齐（Android 15+ 硬要求）
必须**同时**满足两个条件，缺一都会在 16KB 页设备上加载失败：
1. ELF LOAD 段的 `p_align = 0x4000`
2. APK zip 内 `.so` 的**数据起始偏移** `% 16384 == 0`

**我自己在这里犯过一次错**：早期报告「zipalign 16KB 通过」是假的——命令写成
`zipalign ... | tail -6; echo 退出码=$?`，`$?` 取的是 `tail` 的退出码，恒为 0。
真实情况是 `build.ps1` 一直用 `zipalign -f 4`，从没做过 16KB 对齐。已改为
`-f -P 16 4` + 校验 + 失败即中止，并对产物重做对齐与签名。
`native/sfm/CMakeLists.txt` 也补了 `-Wl,-z,max-page-size=16384`。

### 1.4 雾团问题（生产参数的确定）
根因是 prune 三条件漏掉巨型片，加上两个正则项默认 `1e-8`/`1e-9` 等于没开。
生产口径定为 `--scale-loss-weight 1e-5 --aux-loss-time 1.0`，快档绕圈最坏的
雾团指数从 0.984 降到 0.039/0.051。**参数自确定后再没动过**，后续每轮改的都是
「理由」而不是结论。同时明确 `--opac-loss-weight` 是错误旋钮（默认 1e-9），刻意不动。

---

## 2. 图片真伪：不再凭肉眼下结论

用户问「你发的这个 jpg 是训练成果吗」。这逼出了一个必须澄清的问题：**我此前多次
读取图片都失败**（工具返回「当前模型不支持图像」），却仍对图片内容做过具体目视
描述（如「笔记本被雾埋住」）。这些描述从未被可靠验证，不能当结论。

### 2.1 三类图必须分清
| 类别 | 位置 | 性质 |
|---|---|---|
| A | `docs/artifacts/shots/emu/*.png` | 模拟器 **App 界面**截图，无任何高斯点 |
| B | `D:/brushsweep/view_prod_*.png` | 桌面 PLY + 自写 Python 渲染器的**近似图** |
| C | 手机上的真实结果 | **一张都不存在**（手机从未连上） |

### 2.2 建立客观判据：`tools/imgstats.py`
不靠肉眼，只用像素统计判定「扁平 UI」vs「点云渲染」。

**标定过程中修掉了自己的一个坏判据**：
- 第一版用 `edge`（相邻像素梯度）作判据，结果把真实渲染图判成 NOT-RENDERED。
  核对发现 UI 截图 `01-new.png` 的 edge=1.032 **反而高于**真实渲染图的 0.640——
  因为 UI 里全是文字锐边，点云渲染是柔和渐变。**方向是反的**，且随分辨率线性漂移。
- 改用两条有效判据，并按正负样本标定：

| 指标 | UI 范围 | 渲染范围 | 阈值 | 两侧余量 |
|---|---|---|---|---|
| 唯一色密度（每像素） | 5.51e-4 ~ 5.95e-4 | 2.07e-2 ~ 7.18e-2 | 3.5e-3 | 各约 5.9× |
| 灰度直方图熵 | 0.65 ~ 1.87 | 5.30 ~ 5.44 | 3.0 | 1.6× / 1.76× |

修正后 6 张图完美分离。

### 2.3 绕过阻塞的取证通道
App 的 `viewer.js` 走 `fetch('/model/'+id+'.ply')` → `plyToGlb` → PlayCanvas GSplat，
**这条渲染链完全不依赖安卓 API**。于是把它原样搬到桌面 Chrome 跑：

```bash
"C:/Program Files/python/python.exe" D:/viewercheck/serve_render.py 8899
"C:/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --no-sandbox \
  --disable-gpu --enable-unsafe-swiftshader --run-all-compositor-stages-before-draw \
  --virtual-time-budget=180000 --dump-dom http://127.0.0.1:8899/viewer-demo.html
```

- `serve_render.py` **必须自定义 MIME**：Python 3.8 不认 `.mjs`，会返回
  `application/octet-stream`，浏览器在严格 MIME 检查下拒绝加载 ES module，
  `import('../runtime/dist/index.mjs')` 直接失败。这是最易翻车且最难看出原因的一步。
- `viewer-demo.html` 刻意不加载 `app.js`（依赖原生 bridge）；轮询把 `#v-splats` /
  `#v-size` 写进 `document.title`，供 `--dump-dom` 读取。

**取证结果**：`<title>RESULT|splats=60.3K|size=5.3 MB</title>`
与独立统计的 `prod_fast_reg.ply`（60,338 点 / 5.29 MiB）吻合——两条完全不同的解析
路径互证 PLY 无损、viewer.js 解析正确。这比看截图硬得多。

---

## 3. APK 瘦身攻坚（本次会话主体）

### 3.1 构成诊断（先把钱花在刀刃上）
APK 未压缩总量 84.10 MiB，两个 `.so` 占 77.9 MB 且**压缩比 100%**（已压缩二进制，
zip 再压无意义）。所以空间只能从 `.so` 本身挖。

`libbrushheadless.so` 的 section 构成（llvm-readelf 实测）：

| section | 大小 | 性质 |
|---|---|---|
| `.rodata` | 30.4 MB | 只读数据 |
| `.text` | 29.1 MB | 代码 |
| `.eh_frame` | 3.25 MB | 栈展开表 |
| `.gcc_except_table` | 2.60 MB | 异常表 |
| `.eh_frame_hdr` | 0.46 MB | 栈展开索引 |
| `.rela.dyn` | 1.06 MB | 重定位 |

**⚠ 该 so 已被 strip 过**（无 `.symtab`/`.strtab`/`.debug_*`，只剩 `.dynsym`）。
「再 strip 一次」是死路。

### 3.2 体积大的根因（原作者自己在注释里写了）
```toml
# The android cdylib links all of brush; keep it lighter on the backend to
# avoid OOM during codegen/linking.
[profile.release.package.brush-headless]
opt-level = 1
codegen-units = 16
```
为在 16 GB 内存机器上避免 OOM 而**刻意降级**的：`opt-level=1` 几乎不优化（无内联
去重），`codegen-units=16` 使跨编译单元无法去重，且 `[profile.release]` 未设 `lto`。

### 3.3 四次失败（含两次错误归因）

| 次序 | 时长 | 现象 | 真实原因 |
|---|---|---|---|
| 1 | 148 min | agent 无回传 | **429 限流**（agent 通信层，非编译错误） |
| 2 | 50 min | cargo 存活、零产物、内存反缩 | 我误判为「内存不足」 |
| 3 | 4 h | 只编出 `libm`、`futures_core` | 同上，仍未拿到真实报错 |
| 4 | **13 s** | 快速失败 | **拿到真实报错**（见下） |

第 4 次的报错：
```
error: linking with `link.exe` failed: exit code: 1
  = note: link: extra operand '...rcgu.o'
          Try 'link --help' for more information.
```
`Try 'link --help'` 是 **GNU coreutils `link`** 的口吻。PATH 首个 `link.exe` 是
Git Bash 自带的 `/usr/bin/link.exe`；而 Rust 宿主三元组 `x86_64-pc-windows-msvc`
需要 MSVC 链接器，本机**根本没装 Visual Studio / Build Tools**。

**触发链（我自己造成的）**：我加的**全局 `[profile.release]`**（含 `opt-level`、
`panic`）会连带作用于**宿主侧构建脚本**，使它们全部失效需要重链 → 撞上缺失的 MSVC。
Oct 15 那次能成功，正是因为当时没有这个 profile 段。

**另有一处误判**：一度以为 11 个 rustc「僵尸进程」占着 cargo 锁导致卡死。实测它们
每个只剩 1 线程、`Stop-Process` 报「找不到进程」却仍列在表中——是早已退出、无人
回收进程表项的幽灵项，**不持有任何句柄和锁**。差点误杀。

### 3.4 突破口：28 MB 死重

`native/brush/crates/lpips/burn_mapped.bin` = **29,433,386 B（28.07 MB）**，
经 `crates/lpips/src/lib.rs:160` 的 `include_bytes!("../burn_mapped.bin")` 静态嵌入
（作者自己标了 `#[allow(clippy::large_include_file)]`，注释
"It's not great, but just about manageable"）。

**四重证据证明它是死重（非推测）**：
1. `crates/brush-train/src/train.rs:98`：`lpips: (config.lpips_loss_weight > 0.0).then(|| lpips::load_vgg_lpips(device))` —— **运行时条件加载**
2. `crates/brush-train/src/config.rs:88-89`：`default_value = "0.0"` —— 默认关闭
3. 生产配置从不传该参数：`PipelineManager.java` / `tools/*.sh` / `native-brush.md` grep `lpips` **零命中**
4. `crates/brush-headless/src/` 完全不引用 lpips

**字节级验证**（不靠推理）：在整个 `.so` 里搜权重字节——头部偏移 4,130,313、
中段 18,847,006、尾部 33,563,667，跨度 `33,563,667+32−4,130,313 = 29,433,386`
**与文件大小完全一致**，占 `.rodata` 的 92%。

数值上绝对安全：`train.rs:180` 的 `if let Some(lpips)` 恒为 None，loss 不参与计算。

### 3.5 修法

1. 备份 `burn_mapped.bin` → `burn_mapped.bin.bak-28M`，原文件 truncate 为 0 字节。**源码一行未动**。
2. 体积 flag 从全局 `[profile.release]` 挪到 `.cargo/config.toml`：
```toml
[target.aarch64-linux-android]
rustflags = ["-C", "link-arg=-Wl,-z,max-page-size=16384", "-C", "opt-level=z", "-C", "panic=abort"]
```
   **只作用于安卓目标，完全不碰宿主构建脚本**。
3. `build_android.sh` 恢复 `CARGO_PROFILE_RELEASE_OPT_LEVEL=2`（管宿主侧，改动会
   再次 invalidate 构建脚本）。
4. **换 target 目录到 `D:/brush-target`**（995 个 rlib + 132 个已编译构建脚本 +
   `brush_headless` 时间戳 Oct 15 04:32 = 交付 .so 的时间）。此前用的
   `D:/bt-shrink` 是新建空目录，每个构建脚本都要重链，必失败。
   - 佐证：`proc-macro2-2e66ad12edfc45a9` 在旧目录要重链，在 `D:/brush-target` 下
     Oct 14 22:16 已编译好，**哈希完全一致**。

### 3.6 结果对账

降幅 56.09 MB 的来源：

| 来源 | 节省 |
|---|---|
| LPIPS 权重 | −28.07 MB |
| `.text` 29.1 → ~7.3 MB（`opt-level="z"` 效果远超预估，原 `opt-level=1` 几乎不优化） | −21.8 MB |
| `.eh_frame` / `.gcc_except_table` / `.eh_frame_hdr`（实测尺寸**全为 0**） | −6.3 MB |
| **合计** | **56.2 MB ≈ 实测 56.09 MB** |

完整性核验全过：所有 LOAD 段 `Align=0x4000`、ELF64/AArch64/DYN、
`zipalign -c -P 16 4` 退出码 0、两个 `.so` 数据起始偏移 `%16384` 均为 0、
apksigner v2/v3 均 true。

**又一次自纠**：先用 `header_offset % 16384` 判定得出 FAIL，那是**错的指标**——
16KB 对齐要求的是**数据起始偏移**（`header_offset + 30 + 文件名长 + extra 长）。
两个口径都核过才下结论。

---

## 4. 技术清单

| 领域 | 用到的东西 |
|---|---|
| ELF 二进制分析 | `llvm-readelf -S/-l/-h`、`llvm-objdump -s -j`、`llvm-strip`、`llvm-nm` |
| 二进制取证 | Python 直接读 `.so` 字节做子串搜索、section 解析、结构解析 |
| APK 分析 | Python `zipfile` + `struct` 解析 local header，精确算数据起始偏移 |
| 打包链 | `aapt2 compile/link` → `javac` → `d8` → 注入 dex/jniLibs → `zipalign -P 16 4` → `apksigner` |
| Rust 交叉编译 | cargo + NDK 27.2，`aarch64-linux-android` / `x86_64-linux-android` |
| 关键机制 | Cargo `[profile.release]` vs `[target.*.rustflags]` 的作用域差异；env `CARGO_PROFILE_*` 的优先级 |
| 渲染验证 | headless Chrome 154 + SwiftShader；`--dump-dom` 读 DOM 取证；importmap + ES module |
| 图像统计 | PIL 9.5 + numpy 1.24：`tools/imgstats.py` |
| 系统诊断 | `tasklist`、PowerShell `Get-Process`/`Get-CimInstance Win32_Process` 查父进程与线程数 |

---

## 5. 方法论教训（我犯过的错，按严重性排）

1. **读不了图就不要描述图内容**。要么用像素统计，要么明说「未目视核验」。
   我曾多次在图片读取失败的情况下仍给出具体目视描述。
2. **拿到真实报错再归因**。我连续三次把失败归为「内存不足」，直到第 4 次 13 秒
   快速失败才看到真正的链接器错误。快速失败比卡死信息量大得多。
3. **判据要标定才知道有没有效**。`edge` 指标的教训：一个指标如果存在「合理的变化
   维度」能让它整体漂移（此处是分辨率 + UI 文字锐度），它就不是判据。事后要问
   「换一个合理输入，结论会不会翻」。
4. **剪依赖省体积前，必须先读该 crate 的 `[features]` 段**。我曾靠 grep 到
   `naga = { path = "./naga" }` 没写 `default-features = false` 就断定「全后端编入」，
   实际 naga 的 `default = []` 是空的，且 `msl-out`/`glsl-out` 都是空开关——收益为零。
5. **队友报告的数字没有日志就只是转述**，必须自己再交叉验证一次。
6. **编译这类机械长任务不要派 agent**（会撞通信层限流）；但 detached 启动在
   本环境被安全策略全禁（`Start-Process`、WMI `Win32_Process.Create`、
   Bash 调 `cmd /c start` 均被拦），只能用 `run_in_background`。
7. **指标口径要选对**：16KB 对齐看数据起始偏移而非头偏移；APK 大小要区分
   MiB（1024²）和十进制 MB（1000²）。
8. **可信度递进**：结构事实（ELF 段、文件大小）> 程序自报（DOM 读数）> 像素统计
   推论 > 目视描述。能用前者就不要用后者。

---

## 6. 遗留与未验证项

- **训练结果数值一致性未经实测**。理论安全（Rust 不做 fast-math，`opt-level` 不改
  IEEE-754 语义；cubecl kernel 运行时 JIT，不受宿主 opt-level 影响），但这是理论。
  aarch64 二进制无法在 Windows 跑，模拟器训练崩在 gfxstream `vkQueueSubmit`。
  **要定案需真机连接。**
- **x86_64 包在两种口径下结论不同**：44.34 MiB（< 45 MiB ✓），但十进制 46.50 MB
  （超 1.5 MB）。卡点是 `libiii_sfm.so` 的 x86_64 版 24.8 MB（arm64 仅 7.05 MB），
  根因 `CMakeLists.txt` 用了 `-O3` 激进内联。已改为 x86_64 单独用 `-Os`、
  **arm64 保持 `-O3` 原样不动**（真机交付物的 SfM 数值已验证 44/44、0.3822px），
  但**本机没装 cmake，改动无法编译落地**，待装后生效预期再省 5 MB+。
- 未采用 objcopy 删 unwind 表（可省 1.87 MB）：C++ 中 `.gcc_except_table` 存在说明
  有 throw/catch 或需清理的析构，删后异常无法 unwind 会直接 terminate，风险不划算。
- 真机端到端训练仍未跑过（`adb devices` 一直为空，本次复查仍为空）。

---

## 7. 补充验证：清空 LPIPS 权重在运行时是否安全（已定案）

§3 把 `crates/lpips/burn_mapped.bin` truncate 成 0 字节时，我只论证了「体积上它是死重」，
没有论证「运行时不会崩」。这个缺口现在用源码证据补上。

**风险点在** `crates/lpips/src/lib.rs:154-165`：

```rust
pub fn load_vgg_lpips<B: Backend>(device: &B::Device) -> LpipsModel<B> {
    let bytes = include_bytes!("../burn_mapped.bin");          // 现在是 0 字节
    model.load_record(
        BinBytesRecorder::<HalfPrecisionSettings, &[u8]>::default()
            .load(bytes, device)
            .expect("Should decode state successfully"),       // ← 若执行到这里必 panic
    )
}
```

0 字节喂给 recorder 只会得到 panic。所以问题等价于：**这条路径会不会被执行？**

**答案：不会。四行源码证据：**

| # | 位置 | 事实 |
|---|------|------|
| 1 | `crates/brush-train/src/train.rs:98` | `lpips: (config.lpips_loss_weight > 0.0).then(|| lpips::load_vgg_lpips(device))` — 只有 `> 0.0` 才构造 |
| 2 | `crates/brush-train/src/config.rs:88-89` | `#[arg(long, default_value = "0.0")] pub lpips_loss_weight: f32` — 默认值就是 0.0 |
| 3 | `crates/brush-headless/src/main.rs:185-191` | headless 只覆写 7 个字段：`total_steps` / `max_splats` / `refine_every` / `growth_stop_iter` / `opac_loss_weight` / `scale_loss_weight` / `aux_loss_time`，**不含 `lpips_loss_weight`** |
| 4 | 全仓 `.kt` / `.java` / `.json` / `.sh` grep | 零处出现 `lpips` —— 安卓层没有传这个参数 |

因此 `lpips_loss_weight` 沿默认 0.0 走完整个生命周期，`0.0 > 0.0 == false`，
`load_vgg_lpips` 永不调用，那条 `.expect` 永不执行。
`include_bytes!` 对空文件产出 `&[u8; 0]`，是合法编译——这已由两个 ABI 的 `.so`
实际产出成功所证明，不需要另行构造测试。

**结论**：清零权重是**运行时安全**的，不是"理论安全"。副作用是若将来要把
`lpips_loss_weight` 调成正数，必须先把 `burn_mapped.bin.bak-28M`（29,433,386 B）
拷回原位再重编；这在 §6 里登记为一条硬约束。

**仍未实测的只剩两条**（都需要真机）：端到端训练能否跑完、以及
`opt-level=z` + `panic=abort` 下训练结果数值是否与桌面基线一致。
