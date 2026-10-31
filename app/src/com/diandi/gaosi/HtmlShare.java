package com.diandi.gaosi;

import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 「分享 HTML」：把 final.ply 打成与桌面版同方案的<b>单文件自包含 HTML</b>。
 *
 * 产物结构（与桌面单文件 preview 同方案，顺序不能变）：
 *   <script type="application/json" id="viewer-config">     —— viewer 配置
 *   <script type="application/octet-stream" data-ply-chunk> —— PLY base64 分片 × N
 *   <script type="application/octet-stream" id="viewer-runtime"> —— 渲染器 base64
 *   <script>bootstrap：atob 解码 runtime 并注入执行</script>
 *
 * 三条硬约束（违反会导致打开失败，改代码前务必读）：
 *  1. 每片原始 PLY 必须是 786,432 字节，正好编码成 1,048,576 = 2^20 个 base64 字符。
 *     786,432 = 3 × 262,144，是 3 的倍数，保证片边界落在 3 字节对齐处；
 *     片与片之间不留任何分隔符。这个数不能改，改了就破坏对齐性质。
 *  2. splatCount 必须等于 PLY 头里的 element vertex N，runtime 会强校验。
 *  3. view 必须 11 个字段齐全，缺一个相机 restore 就失败。
 *
 * 全程流式：PLY 一次只进 786 KB，绝不整文件读进内存（手机端几十 MB 会 OOM）。
 * 包围盒用 RandomAccessFile 等间隔抽样读，也只读得到采样点那几 KB。
 */
public final class HtmlShare {

    private static final String TAG = "HtmlShare";

    /** 见类注释约束 1，不要改这个数 */
    private static final int CHUNK_BYTES = 786432;

    /** 只用于解析 PLY 头的预读窗口；真实头通常 200~600 字节，64 KB 绰绰有余 */
    private static final int HEAD_WINDOW = 65536;

    /** 包围盒采样点数上限 */
    private static final int SAMPLE_TARGET = 20000;

    /** 包围盒算不出来时的兜底取景 */
    private static final double FALLBACK_DISTANCE = 3.0;
    private static final double FALLBACK_FARCLIP = 500.0;

    /** 与桌面版一致的相机默认项 */
    private static final double DEFAULT_FOV = 52.0;
    private static final double DEFAULT_NEAR_CLIP = 0.0001;
    private static final double DEFAULT_ORTHO_HEIGHT = 10.0;

    /** 保存到手机「下载」时使用的子目录名 */
    private static final String SUBDIR = "点点高斯";
    private static final Pattern RE_VERTEX = Pattern.compile("element\\s+vertex\\s+(\\d+)");
    private static final Pattern RE_ELEMENT = Pattern.compile("element\\s+(\\S+)\\s+(\\d+)");
    private static final Pattern RE_PROPERTY = Pattern.compile("property\\s+(\\S+)\\s+(\\S+)");

    private static final java.util.Map<String, Integer> TYPE_SIZE =
            new java.util.HashMap<String, Integer>();

    static {
        TYPE_SIZE.put("char", 1);
        TYPE_SIZE.put("uchar", 1);
        TYPE_SIZE.put("int8", 1);
        TYPE_SIZE.put("uint8", 1);
        TYPE_SIZE.put("short", 2);
        TYPE_SIZE.put("ushort", 2);
        TYPE_SIZE.put("int16", 2);
        TYPE_SIZE.put("uint16", 2);
        TYPE_SIZE.put("int", 4);
        TYPE_SIZE.put("uint", 4);
        TYPE_SIZE.put("int32", 4);
        TYPE_SIZE.put("uint32", 4);
        TYPE_SIZE.put("float", 4);
        TYPE_SIZE.put("float32", 4);
        TYPE_SIZE.put("double", 8);
        TYPE_SIZE.put("float64", 8);
    }

    /** view 数组下标 */
    private static final int V_DISTANCE = 0, V_FARCLIP = 1, V_FOV = 2, V_FRAME_OFFSET = 3,
            V_NEARCLIP = 4, V_ORTHO_HEIGHT = 5, V_PITCH = 6, V_TX = 7, V_TY = 8, V_TZ = 9,
            V_YAW = 10;
    private static final int VIEW_LEN = 11;

    private HtmlShare() {
    }

    /**
     * 生成单文件 HTML。几十 MB 的 base64 编码是耗时操作，
     * <b>必须由调用方放到后台线程</b>，主线程调用会卡死 UI。
     *
     * @return 生成的 HTML 文件；任何一步失败都返回 null（原因见 logcat）
     */
    public static File build(Context c, String id) {
        File dir = ProjectStore.dir(c, id);
        if (dir == null) {
            Log.w(TAG, "非法项目 id，拒绝生成: " + id);
            return null;
        }
        File ply = new File(dir, "final.ply");
        if (!ply.isFile() || ply.length() < 32) {
            Log.w(TAG, "final.ply 不存在或过小: " + id);
            return null;
        }

        PlyLayout layout = readLayout(ply);
        if (layout == null) {
            Log.w(TAG, "PLY 头解析失败: " + id);
            return null;
        }
        Log.i(TAG, "ply vertexCount=" + layout.vertexCount + " stride=" + layout.stride
                + " dataOffset=" + layout.dataOffset + " size=" + ply.length());

        // 相机参数：先抽样算包围盒（只读采样点，不整文件加载）
        double[] view = estimateView(ply, layout);

        File outDir = new File(c.getCacheDir(), "shared");
        if (!outDir.exists() && !outDir.mkdirs()) {
            Log.w(TAG, "无法创建输出目录 " + outDir);
            return null;
        }
        File out = new File(outDir, safeFileName(projectName(c, id)) + ".html");
        if (out.exists()) out.delete();

        BufferedWriter w = null;
        InputStream in = null;
        try {
            w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(out), StandardCharsets.UTF_8), 1 << 16);

            w.write(head(projectName(c, id)));
            w.write("<script type=\"application/json\" id=\"viewer-config\">");
            w.write(configJson(layout.vertexCount, view));
            w.write("</script>");

            // ---- PLY 分片：每片 786,432 字节 → 1,048,576 个 base64 字符 ----
            byte[] buf = new byte[CHUNK_BYTES];
            in = new FileInputStream(ply);
            long remaining = ply.length();
            while (remaining > 0) {
                int want = (int) Math.min(CHUNK_BYTES, remaining);
                readFully(in, buf, want);
                w.write("<script type=\"application/octet-stream\" data-ply-chunk>");
                w.write(Base64.encodeToString(buf, 0, want, Base64.NO_WRAP));
                w.write("</script>");
                remaining -= want;
            }
            close(in);
            in = null;

            // ---- 渲染器：2 MB 量级，一次性编码可接受 ----
            byte[] rt = readAsset(c, "share/runtime.js");
            if (rt == null || rt.length == 0) {
                Log.w(TAG, "assets/share/runtime.js 读取失败");
                throw new IOException("runtime asset missing");
            }
            w.write("<script type=\"application/octet-stream\" id=\"viewer-runtime\">");
            w.write(Base64.encodeToString(rt, Base64.NO_WRAP));
            w.write("</script>");

            w.write(BOOTSTRAP);
            w.write(licenses());
            w.write("</body></html>");
            w.flush();

            Log.i(TAG, "built " + out.getAbsolutePath() + " (" + out.length() + " bytes)");
            return out;
        } catch (Throwable t) {
            Log.w(TAG, "生成失败: " + t);
            if (out.exists()) out.delete();
            return null;
        } finally {
            close(in);
            close(w);
        }
    }

    // ------------------------------------------------------------------ 头部 / 模板

    /**
     * 文档头 + 内联 CSS + DOM。品牌一律「点点高斯」，不出现他方商标。
     * <p>
     * #logo 必须保留：runtime 里有 document.getElementById('logo').src = ... 这行，
     * 拿到 null 会直接抛异常、整个预览打不开。这里保留元素但 display:none 隐藏，
     * 避免把他方 logo SVG 显示出来（商标约束）。
     * #scene 必须是 canvas：runtime 把它直接传给 createGraphicsDevice / App 构造。
     */
    /**
     * 生成并<b>保存到手机「下载」目录</b>，返回落盘后的可读路径。
     *
     * 与 shareProject 的区别：不弹系统分享面板，直接把 HTML 落到用户能在
     * 文件管理器 / 系统「下载」里看到的位置。
     *
     * 分区存储适配（minSdk 24 / target 36）：
     *  - API 29+：走 MediaStore.Downloads，<b>无需任何权限</b>，写入即出现在系统「下载」
     *  - API 24-28：写外部公共目录，需要 WRITE_EXTERNAL_STORAGE（Manifest 已按
     *    maxSdkVersion=28 声明）。权限由调用方 Bridge 提前申请，这里不重复申请。
     *
     * @return 形如 {@code Download/点点高斯/xxx.html} 的路径；任一步失败返回 null
     */
    public static String saveToDownloads(Context c, String id) {
        File html = build(c, id);
        if (html == null || !html.isFile()) {
            Log.w(TAG, "saveToDownloads: 生成失败 " + id);
            return null;
        }
        String name = html.getName();
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues cv = new ContentValues();
                cv.put(MediaStore.Downloads.DISPLAY_NAME, name);
                cv.put(MediaStore.Downloads.MIME_TYPE, "text/html");
                cv.put(MediaStore.Downloads.RELATIVE_PATH,
                        Environment.DIRECTORY_DOWNLOADS + File.separator + SUBDIR);
                cv.put(MediaStore.Downloads.IS_PENDING, 1);
                Uri uri = c.getContentResolver()
                        .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                if (uri == null) {
                    Log.w(TAG, "saveToDownloads: MediaStore insert 返回 null");
                    return null;
                }
                try (InputStream is = new FileInputStream(html);
                     OutputStream os = c.getContentResolver().openOutputStream(uri)) {
                    if (os == null) throw new IOException("openOutputStream 返回 null");
                    copy(is, os);
                }
                cv.clear();
                cv.put(MediaStore.Downloads.IS_PENDING, 0);
                c.getContentResolver().update(uri, cv, null, null);
                return Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR + "/" + name;
            }
            File dir = new File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                    SUBDIR);
            if (!dir.isDirectory() && !dir.mkdirs()) {
                Log.w(TAG, "saveToDownloads: mkdirs 失败 " + dir);
                return null;
            }
            File dst = new File(dir, name);
            try (InputStream is = new FileInputStream(html);
                 OutputStream os = new FileOutputStream(dst)) {
                copy(is, os);
            }
            return dst.getAbsolutePath();
        } catch (Exception e) {
            Log.w(TAG, "saveToDownloads failed: " + e.getMessage());
            return null;
        }
    }

    /** 流式拷贝，逐块 1 MiB。HTML 可能 90 MB，不能整文件进内存。 */
    private static void copy(InputStream is, OutputStream os) throws IOException {
        byte[] buf = new byte[1 << 20];
        int n;
        while ((n = is.read(buf)) > 0) os.write(buf, 0, n);
        os.flush();
    }

    private static String head(String name) {        return "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,"
                + "maximum-scale=1,user-scalable=no\">"
                + "<title>" + esc(name) + " · 点点高斯</title>"
                + "<style>" + CSS + "</style></head><body>"
                + "<canvas id=\"scene\"></canvas>"
                + "<nav>"
                + "<button id=\"play\" type=\"button\"></button>"
                + "<button id=\"replay\" type=\"button\"></button>"
                + "<button id=\"reset\" type=\"button\"></button>"
                + "<img id=\"logo\" alt=\"\" style=\"display:none\">"
                + "</nav>"
                + "<div id=\"loading\" hidden>"
                + "<div id=\"loading-title\"></div><div id=\"loading-detail\"></div>"
                + "</div>"
                + "<div id=\"status\"></div>";
    }

    /** 许可证块：只保留 PlayCanvas 的 MIT 声明（法律要求），不伪造他方的 Apache-2.0 声明 */
    private static String licenses() {
        return "<details id=\"licenses\"><summary>开源许可</summary>"
                + "<p><b>PlayCanvas Engine</b> — Copyright 2011-2025 PlayCanvas Ltd."
                + " Licensed under the MIT License.</p>"
                + "<p>本文件由「点点高斯」生成，模型与渲染器均已内嵌，可完全离线打开。</p>"
                + "</details>";
    }

    /**
     * viewer-config。view 必须 11 个字段：
     * distance / farClip / fov / horizontalFrameOffset / nearClip / orthoHeight /
     * orthographicView / pitch / projection / target[3] / yaw。
     */
    private static String configJson(int splatCount, double[] v) {
        StringBuilder b = new StringBuilder(256);
        b.append("{\"locale\":\"zh-CN\",\"splatCount\":").append(splatCount).append(",\"view\":{");
        b.append("\"distance\":").append(num(v[V_DISTANCE]));
        b.append(",\"farClip\":").append(num(v[V_FARCLIP]));
        b.append(",\"fov\":").append(num(v[V_FOV]));
        b.append(",\"horizontalFrameOffset\":").append(num(v[V_FRAME_OFFSET]));
        b.append(",\"nearClip\":").append(num(v[V_NEARCLIP]));
        b.append(",\"orthoHeight\":").append(num(v[V_ORTHO_HEIGHT]));
        b.append(",\"orthographicView\":null");
        b.append(",\"pitch\":").append(num(v[V_PITCH]));
        b.append(",\"projection\":0");
        b.append(",\"target\":[").append(num(v[V_TX])).append(',').append(num(v[V_TY]))
                .append(',').append(num(v[V_TZ])).append(']');
        b.append(",\"yaw\":").append(num(v[V_YAW]));
        b.append("}}");
        return b.toString();
    }

    /** 与桌面版一致的 bootstrap：解码 runtime 后原地注入执行 */
    private static final String BOOTSTRAP =
            "<script>const r=document.getElementById('viewer-runtime');"
                    + "const s=document.createElement('script');"
                    + "s.textContent=new TextDecoder().decode("
                    + "Uint8Array.from(atob(r.textContent),c=>c.charCodeAt(0)));"
                    + "r.remove();document.body.append(s);</script>";

    // ------------------------------------------------------------------ PLY 解析

    /** PLY 布局：顶点数、x/y/z 偏移与宽度、记录步长、数据区起始偏移、字节序 */
    private static final class PlyLayout {
        int vertexCount;
        int stride;
        int offX = -1, offY = -1, offZ = -1;
        int sizeX = 4, sizeY = 4, sizeZ = 4;
        long dataOffset;
        boolean bigEndian;
    }

    private static PlyLayout readLayout(File ply) {
        byte[] win = new byte[(int) Math.min(HEAD_WINDOW, ply.length())];
        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(ply, "r");
            raf.readFully(win);
        } catch (Exception e) {
            Log.w(TAG, "PLY 预读失败: " + e.getMessage());
            return null;
        } finally {
            close(raf);
        }

        String head = new String(win, StandardCharsets.ISO_8859_1);
        int end = head.indexOf("end_header");
        if (end < 0) return null;
        int nl = head.indexOf('\n', end);
        if (nl < 0) return null;

        PlyLayout l = new PlyLayout();
        l.dataOffset = nl + 1;
        l.bigEndian = head.indexOf("binary_big_endian") >= 0;

        String[] lines = head.substring(0, end).split("\n");
        boolean inVertex = false;
        int stride = 0;
        for (String raw : lines) {
            String ln = raw.trim();
            Matcher me = RE_ELEMENT.matcher(ln);
            if (me.find()) {
                inVertex = "vertex".equals(me.group(1));
                continue;
            }
            if (!inVertex) continue;
            Matcher mp = RE_PROPERTY.matcher(ln);
            if (!mp.find()) continue;
            String type = mp.group(1), pname = mp.group(2);
            Integer size = TYPE_SIZE.get(type);
            if (size == null) {
                // 列表类型等不支持的属性：布局就此不可推断，直接放弃（宁可报错也不框错地方）
                return null;
            }
            if ("x".equals(pname)) {
                l.offX = stride;
                l.sizeX = size;
            } else if ("y".equals(pname)) {
                l.offY = stride;
                l.sizeY = size;
            } else if ("z".equals(pname)) {
                l.offZ = stride;
                l.sizeZ = size;
            }
            stride += size;
        }
        l.stride = stride;

        Matcher mv = RE_VERTEX.matcher(head.substring(0, end));
        if (!mv.find()) return null;
        try {
            l.vertexCount = Integer.parseInt(mv.group(1));
        } catch (NumberFormatException e) {
            return null;
        }
        if (l.vertexCount <= 0 || stride <= 0 || l.offX < 0 || l.offY < 0 || l.offZ < 0) {
            return null;
        }
        return l;
    }

    /**
     * 抽样算包围盒 → 相机 11 字段。
     * target = 包围盒中心，distance = 对角线 × 1.2，farClip = 对角线 × 10。
     * 采样而非全量：几十 MB 的 PLY 全量加载会 OOM，而且远场飞点会把包围盒撑大，
     * 等间隔采样已经足够定出主体尺度。
     */
    private static double[] estimateView(File ply, PlyLayout l) {
        double[] v = new double[VIEW_LEN];
        v[V_FOV] = DEFAULT_FOV;
        v[V_NEARCLIP] = DEFAULT_NEAR_CLIP;
        v[V_ORTHO_HEIGHT] = DEFAULT_ORTHO_HEIGHT;
        v[V_PITCH] = 0;
        v[V_YAW] = 0;
        v[V_FRAME_OFFSET] = 0;
        v[V_DISTANCE] = FALLBACK_DISTANCE;
        v[V_FARCLIP] = FALLBACK_FARCLIP;
        v[V_TX] = 0;
        v[V_TY] = 0;
        v[V_TZ] = 0;

        double minX = Double.POSITIVE_INFINITY, minY = Double.POSITIVE_INFINITY,
                minZ = Double.POSITIVE_INFINITY;
        double maxX = Double.NEGATIVE_INFINITY, maxY = Double.NEGATIVE_INFINITY,
                maxZ = Double.NEGATIVE_INFINITY;

        int need = Math.min(l.stride, Math.max(Math.max(l.offX, l.offY), l.offZ)
                + Math.max(Math.max(l.sizeX, l.sizeY), l.sizeZ));
        int step = Math.max(1, l.vertexCount / SAMPLE_TARGET);
        byte[] rec = new byte[need];
        ByteOrder order = l.bigEndian ? ByteOrder.BIG_ENDIAN : ByteOrder.LITTLE_ENDIAN;
        int sampled = 0;

        RandomAccessFile raf = null;
        try {
            raf = new RandomAccessFile(ply, "r");
            for (int k = 0; k < l.vertexCount; k += step) {
                long base = l.dataOffset + (long) k * l.stride;
                if (base + need > ply.length()) break;
                raf.seek(base);
                raf.readFully(rec);
                ByteBuffer bb = ByteBuffer.wrap(rec).order(order);
                double x = coord(bb, l.offX, l.sizeX);
                double y = coord(bb, l.offY, l.sizeY);
                double z = coord(bb, l.offZ, l.sizeZ);
                if (Double.isNaN(x) || Double.isNaN(y) || Double.isNaN(z)) continue;
                if (x < minX) minX = x;
                if (x > maxX) maxX = x;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
                if (z < minZ) minZ = z;
                if (z > maxZ) maxZ = z;
                sampled++;
            }
        } catch (Exception e) {
            Log.w(TAG, "包围盒采样中断: " + e.getMessage());
        } finally {
            close(raf);
        }

        if (sampled == 0 || minX > maxX || minY > maxY || minZ > maxZ) {
            Log.w(TAG, "包围盒采样为空，用兜底取景");
            return v;
        }

        double cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
        double dx = maxX - minX, dy = maxY - minY, dz = maxZ - minZ;
        double diag = Math.sqrt(dx * dx + dy * dy + dz * dz);

        v[V_TX] = cx;
        v[V_TY] = cy;
        v[V_TZ] = cz;
        if (diag > 1e-6) {
            v[V_DISTANCE] = diag * 1.2;
            v[V_FARCLIP] = diag * 10;
        } else {
            // 退化成一个点：给个能看见东西的最小取景
            v[V_DISTANCE] = FALLBACK_DISTANCE;
            v[V_FARCLIP] = FALLBACK_FARCLIP;
        }
        Log.i(TAG, "bbox samples=" + sampled + " center=[" + cx + "," + cy + "," + cz + "]"
                + " diag=" + diag + " distance=" + v[V_DISTANCE] + " farClip=" + v[V_FARCLIP]);
        return v;
    }

    private static double coord(ByteBuffer bb, int off, int size) {
        return size == 8 ? bb.getDouble(off) : bb.getFloat(off);
    }

    // ------------------------------------------------------------------ 小工具

    private static byte[] readAsset(Context c, String path) {
        InputStream in = null;
        try {
            in = c.getAssets().open(path);
            ByteArray out = new ByteArray();
            byte[] b = new byte[65536];
            int n;
            while ((n = in.read(b)) > 0) out.write(b, n);
            return out.toBytes();
        } catch (Exception e) {
            Log.w(TAG, "readAsset " + path + " 失败: " + e.getMessage());
            return null;
        } finally {
            close(in);
        }
    }

    /** 极简可增长字节缓冲，避免为读一个 asset 引第三方库 */
    private static final class ByteArray {
        private byte[] a = new byte[65536];
        private int n;

        void write(byte[] src, int len) {
            if (n + len > a.length) {
                int cap = a.length;
                while (cap < n + len) cap <<= 1;
                byte[] b = new byte[cap];
                System.arraycopy(a, 0, b, 0, n);
                a = b;
            }
            System.arraycopy(src, 0, a, n, len);
            n += len;
        }

        byte[] toBytes() {
            byte[] b = new byte[n];
            System.arraycopy(a, 0, b, 0, n);
            return b;
        }
    }

    /** 按 want 读满；不足即视为文件被截断 */
    private static void readFully(InputStream in, byte[] buf, int want) throws IOException {
        int got = 0;
        while (got < want) {
            int n = in.read(buf, got, want - got);
            if (n < 0) throw new IOException("PLY 被截断: 期望 " + want + " 只读到 " + got);
            got += n;
        }
    }

    private static String projectName(Context c, String id) {
        JSONObject m = ProjectStore.meta(c, id);
        String n = m == null ? "" : m.optString("name", "");
        if (n == null || n.trim().isEmpty()) n = "点点高斯";
        return n.trim();
    }

    /** 文件名安全化：去掉路径分隔符与控制字符并截断，避免写出到别的目录 */
    private static String safeFileName(String name) {
        String s = name.replaceAll("[\\\\/:*?\"<>|\\x00-\\x1f]", "_").trim();
        if (s.isEmpty()) s = "点点高斯";
        if (s.length() > 60) s = s.substring(0, 60);
        return s;
    }

    /** 定点小数，避免 String.valueOf 出科学计数法（1.0E-4 之类） */
    private static String num(double v) {
        if (Double.isNaN(v) || Double.isInfinite(v)) return "0";
        return String.format(Locale.US, "%.6f", v);
    }

    private static String esc(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;");
    }

    private static void close(Closeable c) {
        if (c == null) return;
        try {
            c.close();
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------------ 内联 CSS

    /** 内联样式：canvas 铺满、控制条浮层、加载态居中。无任何外链，保证离线。 */
    private static final String CSS =
            "html,body{margin:0;padding:0;height:100%;background:#0e1116;color:#e6edf3;"
                    + "font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
                    + "overflow:hidden;-webkit-text-size-adjust:100%}"
                    + "#scene{display:block;position:fixed;inset:0;width:100%;height:100%;"
                    + "touch-action:none;outline:none}"
                    + "nav{position:fixed;left:50%;transform:translateX(-50%);"
                    + "bottom:calc(16px + env(safe-area-inset-bottom));display:flex;gap:8px;"
                    + "z-index:10;background:rgba(16,20,26,.72);border:1px solid rgba(255,255,255,.12);"
                    + "border-radius:999px;padding:6px;backdrop-filter:blur(8px)}"
                    + "nav button{appearance:none;-webkit-appearance:none;border:0;cursor:pointer;"
                    + "background:transparent;color:#e6edf3;font-size:13px;line-height:1;"
                    + "padding:8px 12px;border-radius:999px;min-height:36px}"
                    + "nav button:disabled{opacity:.4;cursor:default}"
                    + "nav button:not(:disabled):hover{background:rgba(255,255,255,.1)}"
                    + "#loading{position:fixed;inset:0;display:flex;flex-direction:column;"
                    + "align-items:center;justify-content:center;gap:10px;padding:24px;"
                    + "text-align:center;z-index:20;background:#0e1116}"
                    + "#loading[hidden]{display:none}"
                    + "#loading-title{font-size:15px;font-weight:600}"
                    + "#loading-detail{font-size:12px;color:#8b949e;max-width:32em;line-height:1.6}"
                    + "#status{position:fixed;left:12px;top:calc(12px + env(safe-area-inset-top));"
                    + "z-index:10;font-size:11px;letter-spacing:.04em;color:#8b949e;"
                    + "font-family:ui-monospace,SFMono-Regular,Menlo,monospace}"
                    + "#licenses{position:fixed;right:12px;bottom:12px;z-index:10;max-width:60vw;"
                    + "font-size:11px;color:#8b949e;background:rgba(16,20,26,.72);"
                    + "border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:6px 10px}"
                    + "#licenses p{margin:4px 0;line-height:1.6}";
}
