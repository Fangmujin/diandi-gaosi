# 点点高斯 v0.2 —— 引擎命令行契约核验报告

> 核验日期:2026-10-24 | 核验人:team lead | 对象:APP 两条引擎命令行 + 全部工具脚本命令行
> 状态:**发现 2 个 P1（会直接导致功能失败/误报）、3 个 P2、1 个 P3**；引擎命令行本体与当前包内二进制**完全对齐**
> 方法:四层证据链——源码定义 → 二进制字节级 → APK 包内 md5 → Java 传参，逐层闭环，不靠印象

---

## 0. TL;DR

| 问 | 答 |
|---|---|
| APP 现在跑的两条命令行坏了吗？ | **没坏**。包里 4 个 .so 的参数面与 Java 传参 100% 对齐，APK 内二进制与 prebuilt 逐字节一致 |
| 那"有问题"的是什么？ | ① **换机器/换人重建引擎必坏**（CLI 参数透传没进 git）；② **真机验证脚本今天一跑就误报**（字节数闸门过期） |
| 最危险的一条 | `native/brush` 的 git HEAD 干净 checkout 重建出的 `libbrushheadless.so` **没有 `--scale-loss-weight`**，APP 训练阶段会以 clap `unexpected argument` exit 2 直接失败——而现场没有任何文档提过这件事 |

---

## 1. 核验范围与方法

### 1.1 "命令行"范围圈定

Bridge 共 24 个 `@JavascriptInterface` 入口，逐一排查后确认 APP 内**只有 2 条命令行**，都在 `PipelineManager.java`：

| # | 命令 | 构造位置 | 用途 |
|---|---|---|---|
| 1 | `libiii_sfm.so --images --out --max-size --overlap` | PipelineManager.java:651-657 | SfM 位姿重建 |
| 2 | `libbrushheadless.so` + 13 参 | PipelineManager.java:730-744 | 3DGS 训练 |

其余 22 个入口均为文件/权限/UI 操作；`EditStore`/`HttpServer`/`FrameExtractor` 不 exec 任何进程；PLY→GLB 是 JS 侧 wasm（splat-transform），不走命令行。

### 1.2 四层证据链

```
① 源码定义 CLI     brush-headless/src/main.rs (clap, 16 参) + sfm/src/main.cpp (8 参)
        ↓ 比对
② 二进制含 CLI     对 native/prebuilt/{x86_64,arm64-v8a}/*.so 字节级独特长名搜索
        ↓ 比对
③ APK 包内一致     解压 dist/*.apk 的 lib/<abi>/*.so 与 prebuilt 比 md5
        ↓ 比对
④ Java 传参合法    逐参数对照 clap 定义的类型与值域
```

> 环境限制说明：WSL 被本机安全策略拉黑，无法直接执行 x86_64 二进制的 `--help`。第②层用字节级搜索替代——对"参数名是否存在"这一目的，两者等价（clap 的长选项名以明文字符串常驻 `.rodata`）。

---

## 2. 结论总表

### 2.1 引擎命令行本体（功能）

| 命令行 | 参数面 | 退出码契约 | 数据契约 | 总判 |
|---|---|---|---|---|
| SfM（4 参） | ✅ 2/2 ABI 全含 | ✅ 0/2/3/4/5/6/7/8 全映射 | ✅ 文件名链路零漂移 | **✅ 没问题** |
| Train（13 参） | ✅ 14/14 独特长名 2/2 ABI 全含 | ✅ 含 clap 参数错分支 | ✅ control-dir/数据集布局对齐 | **✅ 当前包没问题** |

### 2.2 问题清单

| # | 严重度 | 一句话 | 位置 |
|---|---|---|---|
| P1-1 | **P1 会坏** | brush 源码脏工作区：干净重建出的引擎缺 3 个 CLI 参数，训练必败 | native/brush（未提交改动） |
| P1-2 | **P1 误报** | device_verify.sh 防旧包闸门字节数过期，今天一跑就误杀正确引擎 | tools/device_verify.sh:64 |
| P2-1 | P2 | 取消在导出静默期失灵，最坏延迟=整个导出时长 | PipelineManager.java:965-975 |
| P2-2 | P2 | TMPDIR 在 SfM 阶段指向尚未创建的目录 | PipelineManager.java:934 vs 752-753 |
| P2-3 | P2 | 三处文档/注释与产物脱节（体积、sha256、ABI 覆盖） | native-brush.md / PipelineManager.java:489 / inject_step.py:16 |
| P3-1 | P3 | prebuilt 目录躺着一个 70 MB 的 .bak 备份文件 | native/prebuilt/arm64-v8a/ |

---

## 3. 问题详述

### P1-1 brush 源码仓库脏工作区——干净重建 = 功能必坏

**证据**（`native/brush` 下实测）：

```
$ git log -1 --format="%h %ci %s"
8191177 2026-10-14 22:56:19  Let CARGO_TARGET_DIR override the build target dir

$ git status --short
 M .cargo/config.toml          ← opt-level=z + panic=abort 的 target rustflags（瘦身）
 M Cargo.toml                  ← profile 调整
 M build_android.sh            ← CARGO_BUILD_JOBS 可覆盖 + 注释
 M crates/brush-headless/src/main.rs   ← +18 行：三个损失函数 CLI 参数
 M crates/brush-train/src/config.rs    ← 仅注释
 M crates/lpips/burn_mapped.bin        ← 29,433,386 → 0 字节（权重截断）
?? chunk_build.sh
?? crates/lpips/burn_mapped.bin.bak-28M
```

**决定性比对**：

```
$ git show HEAD:crates/brush-headless/src/main.rs | grep -c scale_loss_weight
0                      ← HEAD 版没有
$ grep -c scale_loss_weight crates/brush-headless/src/main.rs
2                      ← 工作区版有
$ git show HEAD:crates/brush-headless/src/main.rs | grep -c "aux_loss_time\|opac_loss_weight"
0                      ← HEAD 版两个都没有
```

未提交的 +18 行正是这三个参数的 clap 定义与赋值：

```rust
+    #[arg(long, help_heading = "Training options", default_value_t = 1e-9)]
+    opac_loss_weight: f32,
+    #[arg(long, help_heading = "Training options", default_value_t = 1e-8)]
+    scale_loss_weight: f32,
+    #[arg(long, help_heading = "Training options", default_value_t = 0.9)]
+    aux_loss_time: f32,
...
+    args.train_config.opac_loss_weight = cli.opac_loss_weight;
+    args.train_config.scale_loss_weight = cli.scale_loss_weight;
+    args.train_config.aux_loss_time = cli.aux_loss_time;
```

**当前产物是从这个脏工作区构建的**（prebuilt mtime 10-19 18:43/18:55，晚于全部改动；且二进制字节级验证含全部参数），所以**现在跑的包是好的**。

**但后果是双重的**——任何人（或未来的你）在干净 checkout 上 `./build_android.sh` 重建：

| 缺什么 | 后果 |
|---|---|
| main.rs +18 行 | 新 .so 拒绝 `--scale-loss-weight 1e-5`，clap 报 `unexpected argument`，**exit 2，训练阶段必败**。错误信息指向"参数不存在"，而真实原因是"源码没提交"——归因成本极高 |
| .cargo/config.toml + Cargo.toml 瘦身flags | 体积回到 ~70 MB（APK 超 45 MB 预算约 1 倍） |
| burn_mapped.bin 截断 | 带回 29.4 MB 死权重（若传 `--lpips-loss-weight>0` 还会 panic，见 10-19 复盘） |

**修法**（三选一，推荐①）：
1. 把 6 个改动文件 + 2 个未跟踪文件提交进 native/brush 仓库，`README.md` 记录产物对应 commit；
2. 若暂不提交，至少在 `docs/artifacts/native-brush.md` 和 `README.md` 顶部写死警示："**产物绑定未提交工作区，clean checkout 重建前必须先应用 main.rs 的 scale/aux/opac 三参数补丁**"；
3. 把 +18 行补丁存成 `native/brush/patches/0001-headless-loss-cli.patch`，构建脚本自动 `git apply`。

### P1-2 device_verify.sh 防旧包闸门字节数过期——今天一跑就误杀

`tools/device_verify.sh:60-70` 的闸门本意是防"拿着旧引擎试新参数"（注释里写着连栽四次），但写死的期望值是 10-15 04:32 的 70 MB 旧引擎：

```bash
#   70,878,536 = 当前（带 --scale-loss-weight / --aux-loss-time，04:32 产物）
#   70,867,320 / 70,870,192 = 两个历史旧版，均不含新参数
EXPECTED_SIZE=70878536
ACTUAL_SIZE=$("$ADB" shell "stat -c %s $DEV/libbrushheadless.so" ...)
```

**实测当前值**：

| 二进制 | device_verify.sh 期望 | 实际 |
|---|---|---|
| arm64 libbrushheadless.so | 70,878,536 | **14,785,656**（10-19 瘦身后） |

**后果**：脚本 push 的是**正确的新引擎**（已字节级验证含全部参数），却被自己的闸门判为"不是预期版本"→ exit 1。这正是该闸门想防的反向事故：把"新包是对的"演成"引擎是旧的"，一次真机排查还没开始就结束了。

**修法**：`EXPECTED_SIZE=14785656`。更稳的做法是双条件——字节数 + 参数面（设备端 `strings` 不一定有，可改用 `grep -c scale-loss-weight` 对 .so 直接匹配，toybox grep 支持 `-c`）。

### P2-1 取消在导出静默期失灵

`exec()`（PipelineManager.java:962-975）的取消检查只在 **stdout 来新行时**触发：

```java
while ((line = r.readLine()) != null) {
    if (cancelRequested) { writeControl("stop"); Thread.sleep(400); p.destroy(); break; }
    ...
}
```

- 训练中每步都有 JSON，取消敏感 ✅
- **导出阶段 brush 只在小 Ply 写出前后各发一行 JSON，中间几十 MB 落盘是静默的**——这期间用户点取消，检查点根本不到，`stop` 文件和 `destroy()` 都够不到
- 最坏延迟 = 整个导出时长（64 MB 级 ply 在真机上可达分钟级）

**修法**：给读循环加 `readLine` 超时（如 2s 一轮空转检查 cancelRequested），或起一个独立的取消监控线程。改动局限在 `exec()` 内，不碰引擎契约。

### P2-2 TMPDIR 在 SfM 阶段指向不存在的目录

`exec()` 无条件设置 `TMPDIR=<projectDir>/tmp`（934 行），但 `tmp` 只在 `runTrain` 里创建（752-753 行），**晚于 runSfm**。当前 OpenCV 管线全内存操作、不写临时文件所以没爆，属于潜伏雷——一旦引擎或 OpenCV 某条路径要写临时文件就会以难以归因的方式失败。

**修法**：`run()` 开头与 `clearControl(pdir)` 并列加一句 `new File(pdir, "tmp").mkdirs()`。

### P2-3 三处文档/注释与产物脱节

| 位置 | 记载 | 实际 | 风险 |
|---|---|---|---|
| docs/artifacts/native-brush.md | trainer 70,867,320 / 78,215,744 B + 旧 sha256 | 14,785,656 / 18,998,768 B | 按文档核验会误判"产物被换过" |
| PipelineManager.java:489 | "当前 x86_64 只有 trainer、arm64 只有 SfM" | 两 ABI 均双引擎（APK 内实证：x86_64 与 arm64 各含 2 个 .so） | 注释误导排障方向 |
| inject_step.py:16 | 同上（历史教训描述） | 同上 | 同上 |

README.md:167 的 APK 大小口径（47,036,450 B）经复核**仍准确** ✅。

### P3-1 prebuilt 目录的 70 MB 备份

`native/prebuilt/arm64-v8a/libbrushheadless.so.bak-70M`（70,878,536 B，10-18 21:25）。已验证它**不会**被打进包（`inject_step.py` 的过滤器是 `endswith(".so")`，该文件名以 `.bak-70M` 结尾；x86_64 包内只有 2 个 .so）。但它是体积黑洞 + 误判源（`ls` 时容易看错）。建议移到 `native/` 层级或删除（瘦身配方已记录在 git 未提交改动里，可随时重建）。

---

## 4. 核过没病的清单（诚实呈现）

以下每一项都经过上述四层证据链验证，**结论是没问题**，列出以免重复排查：

1. **SfM 参数面**：`--images/--out/--max-size/--overlap` 在两个 ABI 二进制中全部存在（独特长名 5/5 命中，含 `unknown arg` 错误串）；Java 只传这 4 个，未传的 `--features/--exhaustive/--txt/--verbose` 均有默认值。
2. **Train 参数面**：14 个独特长名（export-path/control-dir/total-steps/max-resolution/max-splats/sh-degree/refine-every/growth-stop-iter/opac-loss-weight/scale-loss-weight/aux-loss-time/export-every/max-frames/eval-split-every）在两个 ABI **全部命中**；Java 传 13 参（不传 max-frames/eval-split-every，均为 Option）。
3. **APK 包内一致性**：两个 APK 的 4 个 .so 与 prebuilt **md5 逐字节相同**——"包里的就是我验过的"成立。
4. **SfM 退出码契约**：main.cpp 实际返回 0/2/3/4/5/6/7/8；Java `sfmError`（PipelineManager.java:858-895）对 3/4/5/6/7/8 全有人话映射 + 对 7 的 stderr 嗅探（no valid initial image pair / more parallax）+ 兜底，**无缺口**。
5. **stdout stage 名对账**：SfM 只发 features/matching/mapping/done；Java LineSink 的映射（features→1、matching→2、mapping→3）覆盖全部，done 由 exec 返回后的 `stageIndex=4` 收尾，不会卡阶段。
6. **跨引擎文件名链路零漂移**：FrameExtractor 写 `frame_%04d.jpg` → SfM `listImages` 收 jpg、images.bin 存 **basename**（NUL 结尾，与 brush `read_until(b'\0')` 的读法精确对齐，10-14 的 name_len bug 已修）→ `copyFrames` 原名拷贝进 dataset/images → brush `files_ending_in(name)` 后缀匹配。**注意 brush 对找不到的图只 warn 不报错**（colmap.rs:127-129），名字一旦对不上就是静默少图训练——这是本链路唯一的结构性风险点，当前实现下不会触发。
7. **数据集布局契约**：brush 用 `files_ending_in("cameras.bin")` 定位 sparse 目录（不要求固定 sparse/0），Java 摆的 `dataset/images + dataset/sparse/0` 满足；`std_dataset_11` 实测布局正确（images/frame_*.jpg + sparse/0 三件套）。
8. **control-dir 取消契约**：Java 传 `--control-dir <projectDir>`，取消时写 `stop` 文件到同一目录；brush `control.rs` 只看文件存在性（不看内容）、训练循环每步查 `should_stop`、pause 150ms 轮询、启动时自动清理残留 stop/pause。契约双向对齐 ✅（唯一残留是 P2-1 的静默期问题）。
9. **参数值域**：sh_degree 1/3（u32 合法）、max_splats 1.5M/4M（u32）、total_steps 6000/13000、`"1e-5"`/`"1.0"` 均可按 clap f32 正确解析、aux_loss_time=1.0 与源码语义（全程生效）一致。
10. **二进制加载合规（瘦身后重测）**：4 个 .so 全部 PIE（type=DYN）+ INTERP=/system/bin/linker64 + PT_LOAD 全 0x4000（16KB）对齐——10-19 的 opt-level=z/panic=abort 瘦身**没有破坏** Android 15+ 16KB 页合规性。
11. **SfM 与抽帧的 max-size 关系**：抽帧已按 maxSize 长边降采样，SfM 再收同一个 `--max-size` 是幂等二次降采样；`cam.out_scale = out_width/width` 的内参计算在 orig/processed 双分辨率下自洽。
12. **device_verify.sh 1b 的训练命令**与 APP 快档参数**逐字一致**（6000/960/1.5M/1/100/4000/2000/42 + 1e-5/1.0），桌面基准（prod_fast_reg.ply 60,338 splats）可直接横向比。
13. **工具脚本前置文件**：e2e_run.py 依赖的 `D:/点点高斯/Inputs/54734.mp4`（3,600,201 B）、`build/edit_small.ply`（4,721,615 B）、adb.exe 均在；emu_*.sh 的硬编码路径（SDK/emulator/AVD）经核存在；device_verify.sh 的 APK 英文名与数据集目录均在。
14. **注册率门禁**：Java 解析的 `registered` 字段与 SfM emitProgress/done JSON 的字段名一致；`reg < max(3, tot*0.5)` 的判据合理。

---

## 5. 对 SPEC v0.1 §5 契约的修订建议（v0.2）

| 契约项 | v0.1 | v0.2 建议 |
|---|---|---|
| brush 参数表 | 列了 8 个 | 补全 16 个，标注 Java 实际传 13 个、刻意不传的 3 个及理由 |
| 引擎版本标识 | 无 | 增加"产物 = native/brush commit + 工作区补丁"的双要素标识，并把字节数校验升格为构建门禁 |
| 取消语义 | "工作目录出现 stop → 立即退出" | 修正为"训练每步检查；导出静默期不保证"，APP 侧 400ms destroy 兜底 |
| 图片匹配失败行为 | 未定义 | 明确"brush 对 images.bin 中找不到的图只 warn 跳过"，要求 APP 侧装配后自检文件名 |

---

## 6. 复现命令附录

```bash
ROOT=C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi

# ① 参数面（二进制字节级）
python -c "
args=['export-path','control-dir','total-steps','max-resolution','max-splats','sh-degree','refine-every','growth-stop-iter','opac-loss-weight','scale-loss-weight','aux-loss-time','export-every','max-frames','eval-split-every']
for abi in ['x86_64','arm64-v8a']:
    d=open('$ROOT/native/prebuilt/%s/libbrushheadless.so'%abi,'rb').read()
    print(abi, [a for a in args if d.count(a.encode())==0] or 'ALL PRESENT')"

# ② APK 包内一致性
python -c "
import zipfile,hashlib
z=zipfile.ZipFile('$ROOT/dist/点点高斯-v0.1-x86_64.apk')
print(hashlib.md5(z.read('lib/x86_64/libbrushheadless.so')).hexdigest()==
      hashlib.md5(open('$ROOT/native/prebuilt/x86_64/libbrushheadless.so','rb').read()).hexdigest())"

# ③ git 脏工作区（P1-1 实锤）
cd $ROOT/native/brush && git status --short && \
  git show HEAD:crates/brush-headless/src/main.rs | grep -c scale_loss_weight   # 输出 0 即实锤

# ④ ELF 16KB 对齐
python $ROOT/build/check_elf_align.py $ROOT/native/prebuilt/arm64-v8a/libbrushheadless.so

# ⑤ device_verify.sh 闸门期望值 vs 实际
stat -c %s $ROOT/native/prebuilt/arm64-v8a/libbrushheadless.so   # 14785656 ≠ 70878536
```

---

*本报告所有结论均可由上述命令复现。未做真机运行验证（WSL 被安全策略拦截、真机未连接）——凡标注"当前包没问题"的，证据止于"包内二进制与 prebuilt 逐字节一致 + 参数面齐全"，运行期行为仍需装机确认。*
