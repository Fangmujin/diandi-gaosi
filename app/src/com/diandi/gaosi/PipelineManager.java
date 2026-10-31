package com.diandi.gaosi;

import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * 管道编排：抽帧 → SfM(提特征/匹配/重建) → 训练 → 导出。
 * 两个原生引擎通过 ProcessBuilder 调起，逐行读 stdout JSONL 更新进度。
 * 引擎二进制缺失时抛出 EngineMissing，由调用方转成"引擎未就绪"友好提示，绝不崩溃。
 */
public final class PipelineManager {

    private static final String TAG = "Pipeline";

    public static final String[] STAGE_KEYS = {"extract", "features", "matching", "mapping", "train", "export"};
    public static final String[] STAGE_NAMES = {"抽帧", "提特征", "匹配", "重建", "训练", "导出"};
    /** 名义耗时占比，用于首屏预估与 ETA 兜底 */
    private static final double[] WEIGHTS = {0.04, 0.14, 0.12, 0.16, 0.50, 0.04};

    public static final class Params {
        public int frames;
        public int maxSize;
        public int overlap;
        public int maxResolution;
        public int totalSteps;
        public int maxSplats;
        public int shDegree;
        public int refineEvery;
        public int growthStopIter;
        public int exportEvery;
        public int seed;
        public long nominalSec;
        /**
         * 训练正则参数（`--scale-loss-weight` / `--aux-loss-time`）。
         *
         * ⚠⚠ **必须与新 `libbrushheadless.so` 同批次进包。** 这两个参数是 native-brush 于
         * 2026-10-15 才透传进 headless 的（`crates/brush-headless/src/main.rs`）；
         * **旧 .so 不认识它们，clap 会当未知参数直接退出**（不是忽略，是启动即崩）。
         * 任何一个落后都会让训练阶段立刻失败。打包时四个 .so 的 sha256 必须逐一核对。
         *
         * 取值依据（native-brush 按**生产快档参数** res960/sh1/1.5M/refine100/growth4000 扫描 + 复跑，
         * 即本文件 `paramsFor("fast")` 的原值；**不要再引用二进制默认参数那批扫描**）：
         *   基线 80,948 splats、maxσ 50.1、绕圈最坏雾团 **0.984**（整屏），loss −0.1720；
         *   1e-5 + aux 1.0 两次跑：60,611 / 60,884 splats、maxσ 4.3–10.8、绕圈最坏雾团
         *   **0.039 / 0.051**（判据线 0.3，稳定），loss −0.1680（与基线持平）；
         *   PLY 7.45 MB → 5.55 MB（快档）、21.18 MB → 11.38 MB（精档，89,742 → 48,216 splats）。
         *   我侧同口径复算取景（yaw=30，画布 411×914，fovy=45°）：dist 11.87 → 10.04，
         *   占宽 109.7%（溢出）→ 95.9%（贴边），入画率 97.54% → 98.76%。
         *
         * ⚠ **"splat 数跌幅 <30%"这条红线已改判，别再按它复核。**
         * 生产口径降幅是 **−25%（快档 80,948 → 60,611）/ −46%（精档 89,742 → 48,216）**，
         * 精档仍超 30%，但**那条红线是代理指标**，四条直接证据说明主体没受损：
         *   ① loss 持平：−0.1720 vs −0.1680；
         *   ② p50 尺度只降 19%（真塌方要到 1e-4 的 −61%、1e-3 的归零）；
         *   ③ 渲染无空洞；
         *   ④ fog_split 显示雾的 **96–100%** 来自"无 SfM 点支撑"的 splat —— 删掉的正是这批。
         * **主体保护的判据改用「p50 降幅 + loss 是否持平」，不用 splat 数百分比。**
         * （写在这里是因为：以后有人看到 −25%/−46% 会以为我们没查过。
         *   另：早前注释里的 `137,136 → 33,908（−74%）` 是**二进制默认参数**下的数，已作废。）
         *
         * **回退路径（改这两个常量即可，一行切换；⚠ 不是免费回退，先看代价）**：
         *   1e-5 → **1e-6**：p50 由 −52% 收窄到 **−15%**、loss 由 −2.4% 收窄到 −0.4%、splats 由 −25%
         *   收窄到 −5%（主体少削 37 个百分点）。
         *   ⚠ **代价：最坏视角的雾没有改善。** 同格比较（都取绕圈最坏）：
         *     上界口径 1e-6 = 2.512 vs 基线 1.705（**1e-6 反而更差**）；
         *     典型口径 1e-6 = 0.929 vs 基线 0.984（基本持平）；
         *     而当前生产值 1e-5 的两跑是 0.039 / 0.051（典型最坏，**高度一致**）。
         *   ⚠ **回退档自身不可复现**：1e-6 两跑在最坏视角上差 10×（0.0888 vs 0.9290），
         *   真机单跑看到"有雾/没雾"有一半是运气。**接受的是分布，不是数值。**
         *   （取景侧不受影响：1e-6 两次占宽 97.1%/103.6%、入画率 98.08%/98.30%，与 1e-5 同侧。）
         *   触发条件与改法见 `paramsFor()` 里的回退注释；口径与作废说明见
         *   `docs/artifacts/apk-shell.md` §4.8.4。
         *   ⛔ 旧注释里的"雾团 0.147、p50 +18%"来自已作废的默认参数批次，**p50 生产口径是 −15%**。
         *
         * **`--opac-loss-weight` 刻意不动（维持默认 1e-9）—— 它是错误旋钮**：
         *   调到 1e-6 时雾团不降反升（旧口径：0.499 → **0.62**），因为它杀掉的是小而透明的片，
         *   活下来的大片占比反而更高。不要试图用它去压雾团。
         *   （这个 0.499→0.62 是二进制默认参数下的读数，**生产口径没重扫过 opac**，
         *   但"方向反了"这个结论在两档产物的 maxσ 上也看得见：生产基线 maxσ 50.1，
         *   而 scale 正则把它压到 4.3–10.8 —— 压尺度这条路是通的，压不透明度不是。）
         *
         * **快档与精档用同一组**：精档已有生产口径产物（89,742 → 48,216，PLY 21.18 → 11.38 MB、
         * maxσ 38.85 → 9.76），取景数也落在同侧（占宽 96.1%、入画率 98.83%），但**雾团未单独扫**。
         * 外推的依据是「雾团不随步数变差」—— native-brush（训练相机位姿 + 光栅化，
         * 2000/6000/13000 = 0.271/0.269/0.271；⚠ **这组是旧口径实验参数批次**，
         * 绝对值别引用，只保留"三档持平"这个结构性结论）与 native-sfm（查看页累计雾团指数）
         * 两套独立口径都已确认。真机跑完仍需复核精档雾团。
         *
         * ⚠ **判据线 0.3 的正确读法（别读成"余量够大"）**：
         *   host 侧判决（选 1e-5、不选 1e-6）在 **(0.051, 0.929) 整个区间内不变**（宽 18×），
         *   即**不依赖"线取 0.3"这个具体取值**；但 0.3/0.051 = 5.87× 的余量**小于已观测到的
         *   10.46×/10.68× 波动**，所以它不是安全余量。**真机单次结果无论好坏都不能定档，
         *   只能触发补跑**（每档至少 2 次、最好 3 次，取中位数；稳定是格位属性不是档位属性）。
         */
        public String scaleLossWeight = "1e-5";
        public String auxLossTime = "1.0";
        /** 抽帧目标间隔（秒）：决定相邻帧的基线长度，间隔太小会导致视差不足 */
        public double frameSpacingSec;
        public int minFrames;

        public JSONObject toJson() {
            JSONObject o = new JSONObject();
            try {
                o.put("frames", frames).put("maxSize", maxSize).put("overlap", overlap)
                        .put("maxResolution", maxResolution).put("totalSteps", totalSteps)
                        .put("maxSplats", maxSplats).put("shDegree", shDegree)
                        .put("refineEvery", refineEvery).put("growthStopIter", growthStopIter)
                        .put("exportEvery", exportEvery).put("seed", seed)
                        .put("nominalSec", nominalSec);
            } catch (Exception ignored) {
            }
            return o;
        }
    }

    /** 训练二进制约定：PIE 可执行文件，只能 ProcessBuilder exec，绝不能 System.loadLibrary / dlopen */
    private static final String TRAIN_CMD_NAME = "libbrushheadless.so";

    /** Spec §6 双档参数 */
    public static Params paramsFor(String quality) {
        Params p = new Params();
        boolean fine = "fine".equals(quality);
        p.frames = fine ? 70 : 40;
        p.maxSize = fine ? 1600 : 960;
        p.overlap = fine ? 12 : 9;
        // 关键：抽帧数必须按视频时长折算，而不是固定 40/70 帧。
        // 实测同一段 8.7s 素材：抽 40 帧（间隔 0.2s）→ SfM 报 "no valid initial image pair" 退出码 7；
        // 抽 11 帧（间隔约 0.8s）→ 注册 10/11、2153 个 3D 点。基线太短就没有视差。
        p.frameSpacingSec = fine ? 0.55 : 0.75;
        p.minFrames = 8;
        p.maxResolution = fine ? 1280 : 960;
        p.totalSteps = fine ? 13000 : 6000;
        p.maxSplats = fine ? 4000000 : 1500000;
        p.shDegree = fine ? 3 : 1;
        p.refineEvery = fine ? 200 : 100;
        p.growthStopIter = fine ? 10000 : 4000;
        p.exportEvery = fine ? 3000 : 2000;
        p.seed = 42;
        p.nominalSec = fine ? 8100L : 1950L;
        // 正则参数两档相同（依据见字段注释：雾团不随步数变差，两套独立口径已确认）。
        // 回退档 1e-6 —— 注意这不是免费的，别当成保守选项：
        //   收益：p50 由 −52% 收窄到 −15%，loss 由 −2.4% 收窄到 −0.4%，splats 由 −25% 收窄到 −5%（即主体少削 37 个百分点）。
        //   代价：最坏视角的雾回到基线水平，没有改善 —— 上界口径 1e-6 是 2.512，基线是 1.705（1e-6 反而更差）；
        //         典型口径 1e-6 是 0.929，基线 0.984（基本持平）。
        //   作为参照，当前生产值 1e-5 的典型口径最坏是 0.039，上界口径 0.051。
        // 触发条件（写死，不要放宽）：只有在真机近距离目视确认"主体明显变稀疏"之后才启用 1e-6；
        //   启用后必须重看 yaw 0°–10° —— 两次复跑的最坏角都落在 0° 或 10°。
        // 改法：把下面这行的 "1e-5" 换成 "1e-6"，auxLossTime 保持 "1.0" 不变。
        // （旧注释里的"雾团 0.147、p50 +18%"是已作废的默认参数批次数字，别再引用；生产口径见
        //   docs/artifacts/apk-shell.md §4.8.4 与字段注释。）
        p.scaleLossWeight = "1e-5";
        p.auxLossTime = "1.0";
        return p;
    }

    public static final class EngineMissing extends Exception {
        public final String title;
        public final String hint;

        EngineMissing(String title, String hint) {
            super(title);
            this.title = title;
            this.hint = hint;
        }
    }

    public static final class StageError extends Exception {
        public final String stage;
        public final String hint;
        public final boolean retryable;
        /** 失败类别："" 普通可重试错误；"gpu" = 设备图形驱动/模拟器 GPU 层不支持，重试必然再崩 */
        public final String kind;

        public StageError(String stage, String msg, String hint, boolean retryable) {
            this(stage, msg, hint, retryable, "");
        }

        public StageError(String stage, String msg, String hint, boolean retryable, String kind) {
            super(msg);
            this.stage = stage;
            this.hint = hint;
            this.retryable = retryable;
            this.kind = kind;
        }
    }

    /** 训练器在模拟器上崩在 gfxstream 的 Vulkan 驱动里（native-brush 已归因），真机不受影响。 */
    private static boolean isEmulator() {
        String fp = (Build.FINGERPRINT != null ? Build.FINGERPRINT : "").toLowerCase(Locale.ROOT);
        String hw = (Build.HARDWARE != null ? Build.HARDWARE : "").toLowerCase(Locale.ROOT);
        String md = (Build.MODEL != null ? Build.MODEL : "").toLowerCase(Locale.ROOT);
        return fp.contains("generic") || fp.contains("emulator") || fp.contains("vbox")
                || hw.contains("ranchu") || hw.contains("goldfish")
                || md.contains("sdk_gphone") || md.contains("emulator");
    }

    /**
     * 训练阶段崩溃到底是"设备 GPU 不支持"还是真的引擎错误。
     * 判据：SIGSEGV 退出码，或（模拟器 + stderr 里有 Vulkan/gfxstream/段错误痕迹）。
     */
    private static boolean looksLikeGpuDriverCrash(int code, String stderrTail) {
        if (code == 139 || code == 11 || code < 0 && Math.abs(code) == 11) return true;
        String t = (stderrTail == null ? "" : stderrTail).toLowerCase(Locale.ROOT);
        boolean vulkanish = t.contains("vulkan") || t.contains("gfxstream") || t.contains("vkqueuesubmit")
                || t.contains("segv") || t.contains("segmentation fault") || t.contains("sigsegv");
        return vulkanish && isEmulator();
    }

    /**
     * 训练器"素材没有装配好"的真实签名（native-brush 实测）。
     * 数据集缺 images/ 时，带修复的新二进制不会优雅报错，而是 panic：
     *   Image not found ×N → Loaded 0 training views
     *   → panicked at scene_loader.rs:108: Need at least one view in dataset
     *   → scene_loader.rs:174: Somehow lost data loading channel!      退出码 101
     * 这类必须归 dataset —— 是"素材/抽帧环节没准备好"，绝不能说成设备 GPU 不支持。
     *
     * 注意：判据只看"加载到的视角数"，**不能看 `Image not found` 的条数**。
     * 引擎对图片缺失是优雅降级的：实测 44 帧里删掉 2 张，stderr 有 2 条 Image not found，
     * 但 `Loaded 42 training views`、EXIT=0、PLY 正常产出。按条数拦会把能跑完的任务误杀。
     */
    private static final java.util.regex.Pattern VIEWS_RE =
            java.util.regex.Pattern.compile("loaded\\s+(\\d+)\\s+training views",
                    java.util.regex.Pattern.CASE_INSENSITIVE);

    /**
     * 训练器"素材没有装配好"的真实签名。判据只有一个：**加载到的视角数 N**。
     * 缺 images/ 到一张不剩时，带修复的新二进制不会优雅报错，而是 panic：
     *   Image not found ×N → Loaded 0 training views
     *   → panicked at scene_loader.rs:108: Need at least one view in dataset
     *   → scene_loader.rs:174: Somehow lost data loading channel!      退出码 101
     * 这类必须归 dataset —— 是"素材/抽帧环节没准备好"，绝不能说成设备 GPU 不支持。
     *
     * **绝不能用 `Image not found` 的条数当判据**：引擎对缺图是设计上的优雅降级
     * （`colmap.rs:127` 的注释就是 "If image isn't found, just ignore it. We can still train
     * on the remaining images."，源码层面确认，不是推测）。实测 44 帧里删掉 2 张：stderr 有
     * 2 条 Image not found，但 `Loaded 42 training views`、EXIT=0、PLY 正常产出。
     * 按条数拦会误杀本来会成功的任务，而且比报错更难排查（用户会以为引擎坏了）。
     */
    private static boolean looksLikeDatasetUnready(String engineMsg, String stderrTail) {
        String t = ((engineMsg == null ? "" : engineMsg) + "\n" + (stderrTail == null ? "" : stderrTail))
                .toLowerCase(Locale.ROOT);
        if (t.contains("need at least one view in dataset")
                || t.contains("somehow lost data loading channel")) {
            return true;
        }
        // 取最后一次 Loaded N training views 的 N：N == 0 才拦，N > 0 一律放行
        java.util.regex.Matcher m = VIEWS_RE.matcher(t);
        boolean seen = false;
        int last = -1;
        while (m.find()) {
            seen = true;
            try {
                last = Integer.parseInt(m.group(1));
            } catch (Exception ignored) {
            }
        }
        return seen && last == 0;
    }

    /** 最后一次 `Loaded N training views` 的 N；没出现过返回 -1（在 stderr 全文里找，stderrTail 会截断） */
    private int loadedViews() {
        synchronized (lastStderr) {
            java.util.regex.Matcher m = VIEWS_RE.matcher(lastStderr);
            int last = -1;
            while (m.find()) {
                try {
                    last = Integer.parseInt(m.group(1));
                } catch (Exception ignored) {
                }
            }
            return last;
        }
    }

    /** 引擎优雅降级时缺失的图片帧数（在 stderr 全文里数，stderrTail 只截最后 4000 字会漏计） */
    private int countMissingImages() {
        synchronized (lastStderr) {
            return countOf(lastStderr.toString().toLowerCase(Locale.ROOT), "image not found");
        }
    }

    private static int countOf(String hay, String needle) {
        if (hay == null || needle == null) return 0;
        int n = 0;
        int i = 0;
        while ((i = hay.indexOf(needle, i)) >= 0) {
            n++;
            i += needle.length();
        }
        return n;
    }

    /**
     * "旧包探测器"：contains only ply files / COLMAP dataset directory is required
     * 这两句来自修复前的引擎（ViewSplats 那条 anyhow::bail），新二进制源码树里已经
     * grep 不到（0 处）。所以真机/模拟器上再看到它，只说明一件事：
     * 装的是修复前打出来的旧引擎。这是安装版本问题，不是素材问题、也不是设备问题。
     */
    private static boolean looksLikeStaleEngine(String engineMsg, String stderrTail) {
        String t = ((engineMsg == null ? "" : engineMsg) + "\n" + (stderrTail == null ? "" : stderrTail))
                .toLowerCase(Locale.ROOT);
        return t.contains("contains only ply files") || t.contains("contains only")
                || t.contains("colmap dataset directory is required")
                // clap 的"不认识这个参数"：新版 Java 侧传了 --scale-loss-weight / --aux-loss-time，
                // 而包里的 .so 还是加了这两个参数之前编的。clap 会拒绝并直接退出（不是忽略）。
                // 这是"安装版本过旧"的第三种子类，归到这里而不是 GPU，否则真机上就是一次
                // 无从下手的启动即退。两种 clap 措辞都匹配（新旧版本不同）：
                //   error: unexpected argument '--x' found
                //   error: Found argument '--x' which wasn't expected, or isn't valid in this context
                || t.contains("unexpected argument") || t.contains("wasn't expected");
    }

    private static StageError datasetUnreadyError(String detail) {
        return new StageError("train", "训练器没有读到任何可用的训练视角",
                "素材/抽帧环节没准备好：训练器在这个数据集里一个视角都没加载成功"
                        + (detail == null || detail.isEmpty() ? "" : "（" + brief(detail) + "）")
                        + "。这是数据装配问题，不是设备图形能力问题。请换一段素材重新导入，"
                        + "或检查导入的视频能否正常抽帧；原始日志可展开查看。", false, "dataset");
    }

    private static StageError staleEngineError(String detail) {
        return staleEngineError(detail, null);
    }

    /**
     * 归类为 staleEngine 时必须**同时给出原始 stderr**。
     * 理由：这个分类是我们猜的（按报错串匹配），猜错的可能性始终存在——比如将来我们自己
     * 把参数名写错，也会被判成"引擎过旧"。附上未截断的原始输出，看到的人能立刻分辨
     * 是"未知参数"（clap usage）还是"参数值非法"（另一种报错），误分类就不可怕了。
     *
     * 注：这里的"原始输出"取自 `stderrTail()`，**上限 4000 字**。走 staleEngine 这条路径时
     * stderr 的来源基本是 clap 的参数解析报错，usage 输出通常几百字，**实际不会被截**。
     * （不要为"别的原因触发且 stderr 很长"这种低概率场景去改长度——真要改该动的是
     * "取尾部"这个策略本身，那是另一件事，且会波及 dataset/gpu 两条归因路径。）
     */
    private static StageError staleEngineError(String detail, String rawStderr) {
        String brief = (detail == null || detail.isEmpty()) ? "" : "（" + brief(detail) + "）";
        String raw = "";
        if (rawStderr != null && !rawStderr.trim().isEmpty()) {
            raw = "\n\n原始输出（未截断，用于确认是「未知参数」还是「参数值非法」）：\n"
                    + rawStderr.trim();
        }
        return new StageError("train", "当前安装的引擎版本过旧",
                "训练器报出了只有修复前版本才会出现的错误" + brief
                        + raw
                        + "\n\n这既不是素材问题也不是设备问题，而是安装包里的高斯训练引擎还是旧版。"
                        + "请更新应用到最新版后重新运行本步骤。", false, "staleEngine");
    }

    /** 错误信息摘录：只取最后一行非空的、截到 120 字，避免把整段 panic 糊到界面上 */
    private static String brief(String s) {
        String[] ls = s.split("\n");
        String last = "";
        for (int i = ls.length - 1; i >= 0; i--) {
            String t = ls[i].trim();
            if (!t.isEmpty()) {
                last = t;
                break;
            }
        }
        return last.length() > 120 ? last.substring(0, 120) + "…" : last;
    }

    /**
     * 训练前置检查：与其把不完整的数据集丢给训练器、再回头去猜 stderr，不如启动前自己先验。
     * 契约（native-brush）：--path DIR 需要 DIR/images/*.jpg + DIR/sparse/0/{cameras,images,points3D}.bin
     * 三个 .bin 都必须存在且非空（0 字节的 bin 会让训练器静默地拿到 0 个视角）。
     */
    private void assertDatasetReady(File datasetDir) throws StageError {
        List<String> miss = new ArrayList<String>();
        File images = new File(datasetDir, "images");
        File[] fs = images.listFiles();
        int n = 0;
        if (fs != null) {
            for (int i = 0; i < fs.length; i++) {
                if (fs[i].isFile()) n++;
            }
        }
        if (n <= 0) miss.add("images/（没有图片）");
        File sparse0 = new File(new File(datasetDir, "sparse"), "0");
        String[] need = {"cameras.bin", "images.bin", "points3D.bin"};
        for (int i = 0; i < need.length; i++) {
            File f = new File(sparse0, need[i]);
            if (!f.isFile() || f.length() <= 0) miss.add("sparse/0/" + need[i]);
        }
        if (miss.isEmpty()) {
            logLine("数据集体检通过：" + n + " 张图 · sparse/0 三件套齐全");
            return;
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < miss.size(); i++) {
            if (i > 0) sb.append("、");
            sb.append(miss.get(i));
        }
        throw new StageError("train", "素材未就绪，训练无法开始",
                "启动训练前的检查发现数据集不完整，缺少：" + sb + "。"
                        + "这是素材/抽帧环节的问题，不是设备故障，也不是本应用不支持这台设备。"
                        + "请删除该项目后重新导入一段环绕拍摄、画面稳定、时长 10 秒以上的视频。",
                false, "dataset");
    }

    // ---------- runtime state ----------
    private final Context ctx;
    private final String projectId;
    private final String projectName;
    private final String quality;
    private final Uri source;
    private final Params params;

    private volatile boolean cancelRequested = false;
    private volatile boolean failed;
    private volatile String failStage;
    private volatile String failMsg;
    private volatile String failHint;
    private volatile String failKind = "";
    /** 最近一次引擎 exec 的 stderr 全文（用于失败归因和给用户看原始日志） */
    private final StringBuilder lastStderr = new StringBuilder();

    private int stageIndex = 0;
    private double stageFrac = 0.0;
    private long t0;
    private double etaSmooth = -1;
    private boolean etaCalibrated = false;
    private Double engineEtaSec = null;
    private final List<String[]> logBuf = new ArrayList<String[]>();
    private final SimpleDateFormat lts = new SimpleDateFormat("HH:mm:ss", Locale.CHINA);

    public PipelineManager(Context ctx, String projectId, String projectName,
                           String quality, Uri source) {
        this.ctx = ctx.getApplicationContext();
        this.projectId = projectId;
        this.projectName = projectName;
        this.quality = quality;
        this.source = source;
        this.params = paramsFor(quality);
    }

    public void requestCancel() {
        cancelRequested = true;
    }

    public boolean isCancelled() {
        return cancelRequested;
    }

    public File projectDir() {
        return ProjectStore.dir(ctx, projectId);
    }

    // ---------- main entry ----------
    public void run() {
        t0 = System.currentTimeMillis();
        JSONObject meta = ProjectStore.meta(ctx, projectId);
        try {
            if (meta == null) meta = new JSONObject();
            meta.put("status", "running");
            meta.put("stage", STAGE_NAMES[0]);
            ProjectStore.writeMeta(ctx, projectId, meta);
        } catch (Exception ignored) {
        }

        Throwable err = null;
        try {
            File pdir = projectDir();
            clearControl(pdir);

            // 起飞前体检：引擎不在就立刻给结论，不让用户干等
            File sfmBin = EngineProbe.sfmBin(ctx);
            File trainBin = EngineProbe.trainBin(ctx);
            boolean hasSfm = EngineProbe.ready(sfmBin);
            boolean hasTrain = EngineProbe.ready(trainBin);
            if (!hasSfm || !hasTrain) {
                logLine("engine probe: sfm=" + hasSfm + " train=" + hasTrain + " abi=" + EngineProbe.abi());
                // 必须说清缺的是哪一个：SfM（位姿重建）和 trainer（高斯训练）是两条独立管线，
                // 当前 x86_64 只有 trainer、arm64 只有 SfM，笼统说"引擎未就绪"对用户没有可行动信息。
                String title;
                String which;
                if (!hasSfm && !hasTrain) {
                    title = "重建与训练引擎均未接入";
                    which = "位姿重建引擎（" + EngineProbe.SFM_LIB + "）和高斯训练引擎（" + EngineProbe.TRAIN_LIB + "）";
                } else if (!hasSfm) {
                    title = "位姿重建引擎（SfM）未接入";
                    which = "位姿重建引擎（" + EngineProbe.SFM_LIB + "）";
                } else {
                    title = "高斯训练引擎未接入";
                    which = "高斯训练引擎（" + EngineProbe.TRAIN_LIB + "）";
                }
                throw new EngineMissing(title,
                        "当前设备 ABI 为 " + EngineProbe.abi() + "，缺少 " + which + "。"
                                + "该引擎尚未为这一 ABI 交叉编译完成，接入后重新安装即可开始重建；"
                                + "在此期间导入、图库与设置均可正常使用。");
            }

            // --- 1 抽帧 ---
            enterStage(0);
            File framesDir = new File(pdir, "frames");
            int want = frameBudget();
            logLine("抽帧：素材时长 " + (lastDurationMs > 0 ? (lastDurationMs / 1000.0) + "s" : "未知")
                    + " · 目标帧数 " + want + "（间隔 " + params.frameSpacingSec + "s，上限 "
                    + params.frames + "）");
            int n = FrameExtractor.extract(ctx, source, framesDir, want, params.maxSize,
                    new FrameExtractor.Progress() {
                        @Override
                        public void onProgress(int done, int total, String detail) {
                            setStage(done, total, detail);
                        }

                        @Override
                        public boolean cancelled() {
                            return cancelRequested;
                        }
                    });
            if (cancelRequested) throw cancelEx();
            if (n < 8) {
                throw new StageError("extract", "素材只抽出 " + n + " 帧，不足以重建",
                        "换一段更长的环绕视频，镜头匀速移动且物体始终在画面内", false);
            }
            setStage(n, n, n + " 帧 · " + params.maxSize + "px");
            saveCover(framesDir);
            finishStage(0, 1.0);

            // --- 2-4 SfM ---
            File sfm = sfmBin;
            if (!EngineProbe.ready(sfm)) {
                throw new EngineMissing("重建引擎未就绪",
                        "缺少 " + EngineProbe.SFM_LIB + "（" + EngineProbe.abi() + "）。"
                                + "原生引擎编译完成后重新安装即可，应用其余功能不受影响。");
            }
            // 训练引擎（libbrushheadless.so）的 --path 要求的是标准 COLMAP 数据集布局：
            //   dataset/images/*.jpg + dataset/sparse/0/{cameras,images,points3D}.bin
            // 直接把 SfM 输出目录丢给它会被判成"不是 COLMAP 数据集"而 EXIT=1，
            // 所以这里按契约摆好目录，再把抽帧结果同步进 dataset/images/。
            File datasetDir = new File(pdir, "dataset");
            File dsImages = new File(datasetDir, "images");
            File sparse0 = new File(new File(datasetDir, "sparse"), "0");
            if (!sparse0.exists()) sparse0.mkdirs();
            runSfm(sfm, framesDir, sparse0);
            if (cancelRequested) throw cancelEx();
            copyFrames(framesDir, dsImages);

            // --- 5 训练 ---
            File train = EngineProbe.trainBin(ctx);
            if (!EngineProbe.ready(train)) {
                throw new EngineMissing("训练引擎未就绪",
                        "缺少 " + EngineProbe.TRAIN_LIB + "（" + EngineProbe.abi() + "）。"
                                + "原生引擎编译完成后重新安装即可。");
            }
            enterStage(4);
            File ply = new File(pdir, "final.ply");
            long splats = runTrain(train, datasetDir, ply);
            if (cancelRequested) throw cancelEx();
            finishStage(4, 1.0);

            // --- 6 导出 ---
            enterStage(5);
            if (!ply.exists() || ply.length() < 1024) {
                throw new StageError("export", "模型文件未生成",
                        "训练进程未写出 final.ply，可能是显存不足。可改用快档重试。", true);
            }
            setStage(1, 1, (ply.length() / 1024) + " KB · " + splats + " 高斯点");
            finishStage(5, 1.0);

            JSONObject m = ProjectStore.meta(ctx, projectId);
            if (m == null) m = new JSONObject();
            m.put("status", "done");
            m.put("progress", 100);
            m.put("stage", "已完成");
            m.put("splats", splats);
            m.put("bytes", ply.length());
            m.put("frames", n);
            m.remove("error");
            ProjectStore.writeMeta(ctx, projectId, m);
            publish("done", null, null, null, false);
        } catch (EngineMissing e) {
            err = e;
            fail("engine", e.title, e.hint, false, "engine");
        } catch (StageError e) {
            err = e;
            fail(e.stage, e.getMessage(), e.hint, e.retryable, e.kind);
        } catch (Cancelled e) {
            err = e;
            failed = true;
            failStage = STAGE_KEYS[Math.min(stageIndex, STAGE_KEYS.length - 1)];
            failMsg = "已取消";
            failHint = "";
            JSONObject m = ProjectStore.meta(ctx, projectId);
            if (m != null) {
                try {
                    m.put("status", "failed");
                    m.put("error", "已取消");
                    ProjectStore.writeMeta(ctx, projectId, m);
                } catch (Exception ignored) {
                }
            }
            publish("cancelled", "已取消", "", failStage, false);
        } catch (Throwable e) {
            Log.e(TAG, "pipeline crashed", e);
            err = e;
            String key = STAGE_KEYS[Math.min(stageIndex, STAGE_KEYS.length - 1)];
            fail(key, "管道异常中断：" + safe(e), "可在图库页长按该项目删除后重试", true);
        }
        if (err != null) Log.w(TAG, "pipeline ended with: " + err.getMessage());
    }

    // ---------- 数据集装配 ----------
    /** 把抽帧结果复制成训练器期望的 dataset/images/（images.bin 里记录的就是这些文件名） */
    private void copyFrames(File src, File dst) throws Exception {
        if (!dst.exists() && !dst.mkdirs()) {
            throw new StageError("mapping", "无法创建数据集目录", "设备存储空间不足，清理后重试", true);
        }
        File[] fs = src.listFiles();
        int n = fs == null ? 0 : fs.length;
        for (int i = 0; i < n; i++) {
            File f = fs[i];
            if (!f.isFile()) continue;
            File out = new File(dst, f.getName());
            if (out.exists() && out.length() == f.length()) continue;
            java.io.InputStream in = null;
            java.io.OutputStream os = null;
            try {
                in = new java.io.FileInputStream(f);
                os = new java.io.FileOutputStream(out);
                byte[] buf = new byte[65536];
                int r;
                while ((r = in.read(buf)) > 0) os.write(buf, 0, r);
            } finally {
                if (in != null) try { in.close(); } catch (Exception ignored) { }
                if (os != null) try { os.close(); } catch (Exception ignored) { }
            }
        }
        logLine("数据集装配：" + n + " 帧 → dataset/images，稀疏重建 → dataset/sparse/0");
    }

    // ---------- SfM ----------
    private void runSfm(File bin, File imagesDir, File outDir) throws Exception {
        enterStage(1);
        String[] args = {bin.getAbsolutePath(),
                "--images", imagesDir.getAbsolutePath(),
                "--out", outDir.getAbsolutePath(),
                "--max-size", String.valueOf(params.maxSize),
                "--overlap", String.valueOf(params.overlap)};
        logLine("exec " + bin.getName() + " --images frames --out dataset/sparse/0"
                + " --max-size " + params.maxSize + " --overlap " + params.overlap);

        final int[] registered = {-1};
        final int[] totalImgs = {-1};
        final int[] seen = {0};

        int code = exec(bin, args, projectDir(), new LineSink() {
            @Override
            public void onJson(JSONObject o) {
                String st = o.optString("stage", "");
                int cur = o.optInt("current", -1);
                int tot = o.optInt("total", -1);
                int reg = o.optInt("registered", -1);
                if (reg >= 0) registered[0] = reg;
                if (tot > 0) totalImgs[0] = tot;
                int idx = 1;
                if ("matching".equals(st)) idx = 2;
                else if ("mapping".equals(st) || "reconstruction".equals(st) || "bundle".equals(st)) idx = 3;
                else if ("extract".equals(st) || "features".equals(st)) idx = 1;
                if (idx > stageIndex) {
                    stageIndex = idx;
                    stageFrac = 0.0;
                    lastDetail = "";
                }
                if (cur >= 0 && tot > 0) {
                    setStage(cur, tot, cur + "/" + tot + (reg >= 0 ? " · 已注册 " + reg : ""));
                }
                seen[0]++;
            }

            @Override
            public void onText(String s) {
                logLine(s);
            }
        });

        // 收尾：SfM 没逐阶段报进度时也要把 1..3 标完成
        stageIndex = 4;
        stageFrac = 0.0;
        lastDetail = "";

        if (code != 0) {
            throw sfmError(code, stderrTail());
        }
        File cams = new File(outDir, "cameras.bin");
        File imgs = new File(outDir, "images.bin");
        File pts = new File(outDir, "points3D.bin");
        if (!cams.exists() || !imgs.exists()) {
            throw new StageError("mapping", "位姿解算未产出 COLMAP 结果",
                    "素材视差不足：镜头移动角度太小，或画面重复纹理太少。绕物体走半圈以上再试。", true);
        }
        if (!pts.exists()) logLine("warn: points3D.bin 缺失");

        int tot = totalImgs[0] > 0 ? totalImgs[0] : countFrames();
        int reg = registered[0];
        logLine("SfM 完成：注册 " + reg + "/" + tot);
        if (reg >= 0 && tot > 0 && reg < Math.max(3, tot * 0.5)) {
            throw new StageError("mapping",
                    "只注册了 " + reg + "/" + tot + " 张，素材不足以重建",
                    "画面纹理不足或移动过快。建议：绕物体匀速走一圈、保持 1-2 米距离、"
                            + "避免纯色/反光表面、光线均匀。", true);
        }
    }

    // ---------- train ----------
    // 契约（native-brush）：libbrushheadless.so 是 PIE 可执行文件，只能 exec；
    // stdout 每行一个纯 JSON（含 stage: training|exporting|done|failed），stderr 只是日志。
    private long runTrain(File bin, File datasetDir, File ply) throws Exception {
        // 先把数据集体检做完再 exec：缺图/缺 .bin 时直接给"素材未就绪"，
        // 不用等训练器 panic 之后再从 stderr 里猜原因。
        assertDatasetReady(datasetDir);
        File work = projectDir();
        String[] args = {bin.getAbsolutePath(),
                "--path", datasetDir.getAbsolutePath(),
                "--export-path", ply.getAbsolutePath(),
                "--total-steps", String.valueOf(params.totalSteps),
                "--max-resolution", String.valueOf(params.maxResolution),
                "--max-splats", String.valueOf(params.maxSplats),
                "--sh-degree", String.valueOf(params.shDegree),
                "--refine-every", String.valueOf(params.refineEvery),
                "--growth-stop-iter", String.valueOf(params.growthStopIter),
                "--export-every", String.valueOf(params.exportEvery),
                "--seed", String.valueOf(params.seed),
                // ⚠ 这两个参数需要新 .so 支持；旧 .so 收到会被 clap 当未知参数直接退出。
                "--scale-loss-weight", params.scaleLossWeight,
                "--aux-loss-time", params.auxLossTime,
                "--control-dir", work.getAbsolutePath()};
        logLine("exec " + TRAIN_CMD_NAME + " --path dataset --export-path final.ply"
                + " --total-steps " + params.totalSteps + " --max-resolution " + params.maxResolution
                + " --max-splats " + params.maxSplats + " --sh-degree " + params.shDegree
                + " --seed " + params.seed
                + " --scale-loss-weight " + params.scaleLossWeight
                + " --aux-loss-time " + params.auxLossTime);

        File tmp = new File(work, "tmp");
        if (!tmp.exists()) tmp.mkdirs();

        final long[] splats = {0};
        final int[] iter = {0};
        final String[] fail = {null};

        int code = exec(bin, args, work, new LineSink() {
            @Override
            public void onJson(JSONObject o) {
                String st = o.optString("stage", "");
                if ("failed".equals(st)) {
                    fail[0] = o.optString("error", "训练进程报告失败");
                    logLine("ERROR " + fail[0]);
                    return;
                }
                if ("exporting".equals(st)) {
                    stageIndex = 5;
                    stageFrac = 0.5;
                    lastDetail = "正在写出 final.ply";
                    publish(null, null, null, null, false);
                    return;
                }
                int it = o.optInt("iter", -1);
                int tot = o.optInt("total", -1);
                long sp = o.optLong("splats", -1);
                if (sp > 0) splats[0] = sp;
                if (it >= 0) iter[0] = it;
                Double eta = null;
                if (!o.isNull("eta_sec")) {
                    try {
                        eta = o.getDouble("eta_sec");
                    } catch (Exception ignored) {
                    }
                }
                if (eta != null && it >= 10) engineEtaSec = eta;
                if (it >= 0 && tot > 0) {
                    setStage(it, tot, it + "/" + tot + " 步 · " + fmtCount(sp) + " 高斯点");
                }
            }

            @Override
            public void onText(String s) {
                logLine(s);
            }
        });

        // 缺图是"优雅降级"而不是失败：引擎实测 44 帧里坏 2 张照样 Loaded 42 views、EXIT=0。
        // 所以这里只提示、不拦下——拦下的判据只看视角数（looksLikeDatasetUnready）。
        if (fail[0] == null && !looksLikeDatasetUnready(null, stderrTail())) {
            int missing = countMissingImages();
            if (missing > 0) {
                int views = loadedViews();
                logLine("提示：有 " + missing + " 张画面未能读取，"
                        + (views > 0 ? "已用其余 " + views + " 张继续重建" : "已用其余张画面继续重建")
                        + "（引擎对缺图是设计上的优雅降级，结果可能不完整）。本次不会被拦下；"
                        + "如果成片明显缺一块，再换一段素材重跑。");
            }
        }

        if (fail[0] != null) {
            String tail = stderrTail();
            // 归因顺序很重要：能定位到真实原因的（素材/旧引擎）必须排在"设备不支持"之前，
            // 否则模拟器上任何训练失败都会被笼统说成 GPU 问题，把真 bug 盖住。
            if (looksLikeDatasetUnready(fail[0], tail)) throw datasetUnreadyError(fail[0]);
            if (looksLikeStaleEngine(fail[0], tail)) throw staleEngineError(fail[0], tail);
            if (isEmulator()) throw gpuUnsupportedError(code, fail[0]);
            throw new StageError("train", "训练失败：" + fail[0],
                    "引擎报告了失败原因，通常是显存/内存不足或数据集异常。改用快档（更少帧数、更低分辨率）重试。", true);
        }
        if (code != 0 && (!ply.exists() || ply.length() < 1024)) {
            String tail = stderrTail();
            if (looksLikeDatasetUnready(null, tail)) throw datasetUnreadyError(tail);
            if (looksLikeStaleEngine(null, tail)) throw staleEngineError(tail, tail);
            if (isEmulator()) throw gpuUnsupportedError(code, null);
            throw new StageError("train", "训练中断（退出码 " + code + "）",
                    "可能是内存不足或步进参数过大。改用快档（更少帧数、更低分辨率）重试。", true);
        }
        return splats[0];
    }

    /**
     * 抽帧数 = min(档位上限, max(下限, 时长/目标间隔))。
     * 固定帧数在短视频上会把间隔压到 0.2s，相邻帧几乎没有基线，SfM 直接
     * "no valid initial image pair"（退出码 7）——这是实测踩出来的。
     */
    private long lastDurationMs = -1;

    private int frameBudget() {
        lastDurationMs = -1;
        try {
            lastDurationMs = FrameExtractor.duration(ctx, source);
        } catch (Exception ignored) {
        }
        if (lastDurationMs <= 0) return params.frames;
        double usable = lastDurationMs / 1000.0 * 0.92; // 抽帧区间是 4%~96%
        int want = (int) Math.round(usable / params.frameSpacingSec) + 1;
        return Math.max(params.minFrames, Math.min(params.frames, want));
    }

    /**
     * 把 SfM 的退出码翻译成人话。native-sfm 契约：0 成功 / 2 参数错 / 3 读不到图 /
     * 4 特征不足 / 5,6 匹配失败 / 7 重建失败 / 8 写文件失败。
     * 除 8 之外重试同一段素材都不会变好（素材本身的问题），所以一律 retryable=false，
     * 避免给用户一个点了必然再失败的按钮。
     */
    private StageError sfmError(int code, String tail) {
        String stage = STAGE_KEYS[Math.min(stageIndex, 5)];
        String low = (tail == null ? "" : tail).toLowerCase(Locale.ROOT);
        if (code == 7 || low.contains("no valid initial image pair") || low.contains("more parallax")) {
            return new StageError("mapping",
                    "位姿解算失败：镜头视差不足（退出码 " + code + "）",
                    "相邻画面之间的位移太小，无法解算出相机位姿——引擎原话是 "
                            + "“no valid initial image pair（需要更大的视差或更多有效帧）”。"
                            + "请重新拍摄：绕物体匀速走半圈以上、每步之间明显换个角度、"
                            + "保持 1-2 米距离并让主体充满画面。同一段素材重跑不会变好。",
                    false, "parallax");
        }
        if (code == 4) {
            return new StageError("features", "特征点不足（退出码 4）",
                    "画面缺少可追踪的纹理。避开纯色墙面、玻璃/镜面反光和过曝区域，光线要均匀。", false, "");
        }
        if (code == 5 || code == 6) {
            return new StageError("matching", "特征匹配失败（退出码 " + code + "）",
                    "画面变化过大或重复纹理太多。移动放慢一些，让相邻帧有足够重叠区域。", false, "");
        }
        if (code == 3) {
            return new StageError("extract", "素材读取失败（退出码 3）",
                    "抽出的帧无法被引擎读取。换一段常见编码（H.264）的视频再试。", false, "");
        }
        if (code == 8) {
            return new StageError("mapping", "结果写入失败（退出码 8）",
                    "存储空间不足或目录不可写。清理空间后重试。", true, "");
        }
        return new StageError(stage, "位姿解算失败（退出码 " + code + "）",
                "画面纹理不足、移动过快或光线变化太大都会导致匹配失败。", false, "");
    }

    private String stderrTail() {
        synchronized (lastStderr) {
            int n = lastStderr.length();
            return n > 4000 ? lastStderr.substring(n - 4000) : lastStderr.toString();
        }
    }

    private static String stageNameOf(String key) {
        if (key == null) return "";
        for (int i = 0; i < STAGE_KEYS.length; i++) {
            if (STAGE_KEYS[i].equals(key)) return STAGE_NAMES[i];
        }
        return "engine".equals(key) ? "引擎检查" : key;
    }

    /**
     * 训练阶段的设备图形栈失败。必须说清三件事：发生在"训练"阶段、原因是设备 GPU 驱动不支持、
     * 以及这不是 APP 坏了。retryable=false —— 重试必然再崩，给按钮就是在骗用户。
     */
    private StageError gpuUnsupportedError(int code, String engineMsg) {
        String dev = (Build.MODEL == null ? "当前设备" : Build.MODEL) + (isEmulator() ? "（模拟器）" : "");
        String codeHint = code != 0 ? "（进程退出码 " + code + "）" : "";
        String msg = "训练阶段无法在当前设备上运行" + codeHint
                + (engineMsg != null ? "：" + engineMsg : "");
        String hint = "这台设备（" + dev + "）的图形驱动不支持训练所需的 GPU 计算（Vulkan）。"
                + "这是设备/模拟器图形层的限制，不是应用故障：前面的抽帧与位姿重建已经跑完，"
                + "请在支持 Vulkan 的真机上重新运行本步骤。模拟器上无法完成训练，重试也不会成功。";
        return new StageError("train", msg, hint, false, "gpu");
    }

    // ---------- exec helper ----------
    private interface LineSink {
        void onJson(JSONObject o);

        void onText(String s);
    }

    private int exec(File bin, String[] args, File workDir, LineSink sink) throws Exception {
        ProcessBuilder pb = new ProcessBuilder(args);
        pb.directory(workDir);
        // stdout 保持纯净（引擎约定 stdout 只有 JSON），stderr 单独开线程收日志，避免管道写满卡死
        pb.redirectErrorStream(false);
        try {
            pb.environment().put("LD_LIBRARY_PATH", EngineProbe.libDir(ctx).getAbsolutePath());
            pb.environment().put("TMPDIR", new File(workDir, "tmp").getAbsolutePath());
        } catch (Exception ignored) {
        }
        lastStderr.setLength(0);
        Process p = pb.start();
        final BufferedReader err = new BufferedReader(
                new InputStreamReader(p.getErrorStream(), StandardCharsets.UTF_8));
        Thread errThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String l;
                    while ((l = err.readLine()) != null) {
                        String t = l.trim();
                        if (!t.isEmpty()) {
                            logLine(t);
                            synchronized (lastStderr) {
                                if (lastStderr.length() < 200000) lastStderr.append(t).append('\n');
                            }
                        }
                    }
                } catch (Exception ignored) {
                }
            }
        }, "engine-stderr");
        errThread.setDaemon(true);
        errThread.start();

        BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8));
        String line;
        while ((line = r.readLine()) != null) {
            if (cancelRequested) {
                writeControl("stop");
                try {
                    Thread.sleep(400);
                } catch (InterruptedException ignored) {
                }
                p.destroy();
                break;
            }
            String t = line.trim();
            if (t.isEmpty()) continue;
            if (t.charAt(0) == '{') {
                try {
                    sink.onJson(new JSONObject(t));
                } catch (Exception e) {
                    sink.onText(t);
                }
            } else {
                sink.onText(t);
            }
            publish(null, null, null, null, false);
        }
        try {
            r.close();
        } catch (Exception ignored) {
        }
        if (cancelRequested) {
            try {
                p.destroy();
            } catch (Exception ignored) {
            }
            return -1;
        }
        int code;
        try {
            code = p.waitFor();
        } catch (InterruptedException e) {
            p.destroy();
            code = -1;
        }
        return code;
    }

    private void writeControl(String name) {
        try {
            File f = new File(projectDir(), name);
            java.nio.file.Files.write(f.toPath(), new byte[]{49});
            logLine("control: " + name);
        } catch (Exception ignored) {
        }
    }

    private void clearControl(File dir) {
        for (String n : new String[]{"stop", "pause"}) {
            File f = new File(dir, n);
            if (f.exists()) f.delete();
        }
    }

    // ---------- stage bookkeeping ----------
    private void enterStage(int i) {
        stageIndex = i;
        stageFrac = 0.0;
        engineEtaSec = null;
        JSONObject m = ProjectStore.meta(ctx, projectId);
        if (m != null) {
            try {
                m.put("stage", STAGE_NAMES[i]);
                ProjectStore.writeMeta(ctx, projectId, m);
            } catch (Exception ignored) {
            }
        }
        logLine("── " + STAGE_NAMES[i] + " ──");
        publish(null, null, null, null, false);
    }

    private void setStage(int cur, int tot, String detail) {
        if (tot > 0) stageFrac = Math.max(0.0, Math.min(1.0, (double) cur / (double) tot));
        lastDetail = detail == null ? "" : detail;
        publish(null, null, null, null, false);
    }

    private void finishStage(int i, double f) {
        stageIndex = Math.min(STAGE_KEYS.length - 1, i + 1);
        stageFrac = 0.0;
        lastDetail = "";
        publish(null, null, null, null, false);
    }

    private void fail(String stage, String msg, String hint, boolean retryable) {
        fail(stage, msg, hint, retryable, "");
    }

    private void fail(String stage, String msg, String hint, boolean retryable, String kind) {
        failed = true;
        failStage = stage;
        failMsg = msg;
        failHint = hint;
        failKind = kind == null ? "" : kind;
        JSONObject m = ProjectStore.meta(ctx, projectId);
        if (m != null) {
            try {
                m.put("status", "failed");
                m.put("error", msg);
                m.put("errorKind", failKind);
                ProjectStore.writeMeta(ctx, projectId, m);
            } catch (Exception ignored) {
            }
        }
        logLine("ERROR [" + stage + "] " + msg);
        publish("failed", msg, hint, stage, retryable);
    }

    // ---------- progress publish ----------
    private void publish(String state, String errMsg, String errHint, String errStage, boolean retryable) {
        JSONObject o = new JSONObject();
        try {
            o.put("active", true);
            o.put("projectId", projectId);
            o.put("projectName", projectName);
            o.put("quality", quality);
            o.put("state", state == null ? (failed ? "failed" : "running") : state);
            o.put("stageIndex", stageIndex);
            o.put("elapsedMs", System.currentTimeMillis() - t0);

            double overall = 0;
            for (int i = 0; i < stageIndex && i < WEIGHTS.length; i++) overall += WEIGHTS[i];
            overall += WEIGHTS[Math.min(stageIndex, WEIGHTS.length - 1)] * stageFrac;
            overall = Math.max(0, Math.min(1, overall));
            o.put("overall", overall);

            long elapsedSec = (System.currentTimeMillis() - t0) / 1000L;
            double eta;
            if (engineEtaSec != null && stageIndex == 4) {
                eta = engineEtaSec + WEIGHTS[5] * params.nominalSec;
                etaCalibrated = true;
            } else if (elapsedSec > 8 && overall > 0.02) {
                eta = elapsedSec / overall * (1.0 - overall);
                etaCalibrated = true;
            } else {
                eta = params.nominalSec * (1.0 - overall);
            }
            etaSmooth = etaSmooth < 0 ? eta : (etaSmooth * 0.7 + eta * 0.3);
            o.put("etaSec", Math.max(0, Math.round(etaSmooth)));
            o.put("etaCalibrated", etaCalibrated);
            o.put("nominalSec", params.nominalSec);

            JSONArray stages = new JSONArray();
            for (int i = 0; i < STAGE_KEYS.length; i++) {
                JSONObject s = new JSONObject();
                s.put("key", STAGE_KEYS[i]);
                s.put("name", STAGE_NAMES[i]);
                String st;
                if (failed && STAGE_KEYS[i].equals(failStage)) st = "failed";
                else if (i < stageIndex) st = "done";
                else if (i == stageIndex && !failed) st = "running";
                else st = "pending";
                s.put("status", st);
                double pct = i < stageIndex ? 100 : (i == stageIndex ? Math.round(stageFrac * 1000.0) / 10.0 : 0);
                s.put("pct", pct);
                s.put("detail", i == stageIndex ? lastDetail : "");
                stages.put(s);
            }
            o.put("stages", stages);

            JSONArray lg = new JSONArray();
            synchronized (logBuf) {
                int from = Math.max(0, logBuf.size() - 200);
                for (int i = from; i < logBuf.size(); i++) {
                    JSONObject e = new JSONObject();
                    e.put("t", logBuf.get(i)[0]);
                    e.put("msg", logBuf.get(i)[1]);
                    lg.put(e);
                }
            }
            o.put("log", lg);
            if (errMsg != null) {
                JSONObject e = new JSONObject();
                e.put("stage", errStage == null ? "" : errStage);
                e.put("stageName", stageNameOf(errStage));
                e.put("kind", failed ? failKind : "");
                e.put("message", errMsg);
                e.put("hint", errHint == null ? "" : errHint);
                e.put("retryable", retryable && !"gpu".equals(failKind));
                e.put("raw", stderrTail());
                o.put("error", e);
            }
        } catch (Exception ignored) {
        }
        AppState.setProgress(o.toString());

        // 项目卡片进度只在整百分点变化时落盘，避免训练高频输出打爆 I/O
        int pct = (int) Math.round(o.optDouble("overall") * 100);
        if (pct != lastMetaPct) {
            lastMetaPct = pct;
            JSONObject m = ProjectStore.meta(ctx, projectId);
            if (m != null && !"done".equals(m.optString("status"))
                    && !"failed".equals(m.optString("status"))) {
                try {
                    m.put("progress", pct);
                    ProjectStore.writeMeta(ctx, projectId, m);
                } catch (Exception ignored) {
                }
            }
        }
    }

    private volatile String lastDetail = "";
    private int lastMetaPct = -1;

    private void logLine(String s) {
        synchronized (logBuf) {
            logBuf.add(new String[]{lts.format(new Date()), s});
            if (logBuf.size() > 500) logBuf.subList(0, logBuf.size() - 400).clear();
        }
    }

    private int countFrames() {
        File[] fs = new File(projectDir(), "frames").listFiles();
        return fs == null ? 0 : fs.length;
    }

    private void saveCover(File framesDir) {
        File[] fs = framesDir.listFiles();
        if (fs == null || fs.length == 0) return;
        java.util.Arrays.sort(fs);
        File cover = new File(projectDir(), "cover.jpg");
        try {
            java.nio.file.Files.copy(fs[fs.length / 2].toPath(), cover.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (Exception ignored) {
        }
    }

    private static String fmtCount(long n) {
        if (n <= 0) return "—";
        if (n >= 1000000) return String.format(Locale.US, "%.1fM", n / 1000000.0);
        if (n >= 1000) return String.format(Locale.US, "%.0fK", n / 1000.0);
        return String.valueOf(n);
    }

    private static String safe(Throwable e) {
        String m = e.getMessage();
        return m == null ? e.getClass().getSimpleName() : m;
    }

    private static final class Cancelled extends Exception {
        Cancelled() {
            super("cancelled");
        }
    }

    private Cancelled cancelEx() {
        return new Cancelled();
    }
}
