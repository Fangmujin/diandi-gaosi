package com.diandi.gaosi;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

/**
 * 3DGS 编辑功能数据层：deleted-{rev}.mask 位图 + crop/transform 元数据 + edit.ply 破坏性导出。
 *
 * 与桌面版 OOOSplat 完全同构，改代码前务必先看这三条硬约束：
 *
 * 1. mask 文件（{projectDir}/edits/deleted-{revision}.mask）52 字节头 + LSB-first 位图：
 *      0x00 char[8] "OOSMASK\0"   0x08 u32 version=1   0x0C u32 revision
 *      0x10 u32 reserved=0        0x14 u64 sourceSplatCount
 *      0x1C u64 deletedCount      0x24 u64 maskBytes    0x2C u64 checksum(FNV-1a-64 of payload)
 *      0x34 u8[] payload，bit i = payload[i>>3] >> (i&7) & 1，1 = 已删除
 *    文件总长 = 52 + maskBytes。写入必须 tmp + rename 原子替换。
 *
 * 2. crop 语义是「保留区域内」，由桌面 shader 原文定案：
 *      all(lessThanEqual(abs(center - cropCenter), cropSize * 0.5))   // 盒
 *      distance(center, cropCenter) <= cropRadius                     // 球
 *    kind 映射 null→0（无裁剪全保留）/ sphere→1 / box→2。
 *
 * 3. 预览非破坏性（final.ply 永不改动，只多一个 mask，渲染时按 bit 跳过）；
 *    导出才是破坏性的：写 .edit-{rev}.ply.tmp → rename 成 edit.ply，
 *    只改 header 的 element vertex N，保留顶点字节原样复制。
 *
 * 4. transform 目前<b>不参与导出</b>，只存元数据、由渲染侧即时应用（与桌面版一致）。
 *    真要烘焙进 PLY 时，顺序必须与投影拾取一致：T·R·S，R = Rz(rz)·Ry(ry)·Rx(rx)，
 *    绕原点（PlayCanvas Mat4.setFromEulerAngles 的顺序，JS 侧已对拍确认）。
 *    烘焙不可逆，需求明确前不要开这个口子。
 */
public final class EditStore {

    private static final String TAG = "EditStore";

    /** mask 头部固定 52 字节 */
    static final int HEADER_BYTES = 52;

    private static final byte[] MAGIC = {'O', 'O', 'S', 'M', 'A', 'S', 'K', 0};
    private static final int MASK_VERSION = 1;

    private static final String EDITS_DIR = "edits";
    private static final String MASK_PREFIX = "deleted-";
    private static final String MASK_SUFFIX = ".mask";
    private static final String PLY_NAME = "final.ply";
    private static final String EDIT_PLY_NAME = "edit.ply";

    /** crop 种类，与桌面 shader 的 uOoosplatCropKind 一一对应 */
    static final int CROP_NONE = 0;
    static final int CROP_SPHERE = 1;
    static final int CROP_BOX = 2;

    /** PLY 标量 property 的字节宽度，缺的类型一律返回 -1（不支持就报错，不猜） */
    private static final String[] SCALAR_TYPES = {
            "char", "uchar", "short", "ushort", "int", "uint", "float", "double",
            "int8", "uint8", "int16", "uint16", "int32", "uint32", "float32", "float64"
    };
    private static final int[] SCALAR_SIZES = {
            1, 1, 2, 2, 4, 4, 4, 8,
            1, 1, 2, 2, 4, 4, 4, 8
    };

    private static final byte[] END_HEADER = "end_header".getBytes(StandardCharsets.US_ASCII);

    private EditStore() {
    }

    // ==================== 对外 API（Context 层） ====================

    /**
     * 编辑状态快照。
     * 返回 {ok, revision, sourceSplatCount, deletedCount, crop, transform, hasMask, editPlyExists}。
     * id 不合法 / 目录为 null 时返回 {"ok":false,"message":".."}。
     */
    public static JSONObject state(Context c, String id) {
        JSONObject out = new JSONObject();
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            return fail(out, "项目 id 不合法：" + id);
        }
        try {
            JSONObject m = ProjectStore.meta(c, id);
            JSONObject ed = m == null ? null : m.optJSONObject("editing");
            out.put("ok", true);
            out.put("revision", ed == null ? 0 : ed.optInt("revision", 0));
            out.put("sourceSplatCount", ed == null ? 0L : ed.optLong("sourceSplatCount", 0L));
            out.put("deletedCount", ed == null ? 0L : ed.optLong("deletedCount", 0L));
            JSONObject crop = ed == null ? null : ed.optJSONObject("crop");
            out.put("crop", crop == null ? JSONObject.NULL : crop);
            out.put("transform", transformOf(m));
            out.put("hasMask", latestMask(d) != null);
            out.put("editPlyExists", new File(d, EDIT_PLY_NAME).exists());
        } catch (Exception e) {
            Log.w(TAG, "state failed: " + e.getMessage());
            return fail(out, "读取编辑状态失败：" + e.getMessage());
        }
        return out;
    }

    /** 读最新 mask 的 payload（不含 52 字节头）。没有 / 校验不过都返回 null。 */
    public static byte[] readMask(Context c, String id) {
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            Log.w(TAG, "readMask: 项目 id 不合法 " + id);
            return null;
        }
        File f = latestMask(d);
        if (f == null) return null;
        return readMaskFile(f);
    }

    /**
     * 写 mask 并推进 revision。自己算 deletedCount/maskBytes/checksum，原子落盘，删掉旧 revision 的文件。
     * 校验失败（长度不等于 ceil(sourceSplatCount/8)、或尾部越界位置位）返回 false。
     */
    public static boolean writeMask(Context c, String id, byte[] payload, int revision,
                                    long sourceSplatCount) {
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            Log.w(TAG, "writeMask: 项目 id 不合法 " + id);
            return false;
        }
        byte[] file = buildMaskFile(payload, revision, sourceSplatCount);
        if (file == null) return false;

        File edits = new File(d, EDITS_DIR);
        if (!edits.exists() && !edits.mkdirs()) {
            Log.w(TAG, "writeMask: 无法创建 edits 目录 " + edits);
            return false;
        }
        File tmp = new File(edits, "." + MASK_PREFIX + revision + MASK_SUFFIX + ".tmp");
        File dst = new File(edits, MASK_PREFIX + revision + MASK_SUFFIX);
        try {
            java.nio.file.Files.write(tmp.toPath(), file);
        } catch (IOException e) {
            Log.w(TAG, "writeMask: 写临时文件失败 " + e.getMessage());
            return false;
        }
        if (!atomicReplace(tmp, dst)) {
            Log.w(TAG, "writeMask: rename 失败 " + dst);
            return false;
        }
        // 旧 revision 的 mask 一律清掉，目录下只留当前版本
        File[] olds = edits.listFiles();
        if (olds != null) {
            for (File f : olds) {
                if (!f.equals(dst) && f.getName().startsWith(MASK_PREFIX)
                        && f.getName().endsWith(MASK_SUFFIX)) {
                    f.delete();
                }
            }
        }

        JSONObject m = ProjectStore.meta(c, id);
        if (m == null) m = new JSONObject();
        try {
            JSONObject ed = m.optJSONObject("editing");
            if (ed == null) ed = new JSONObject();
            ed.put("revision", revision);
            ed.put("sourceSplatCount", sourceSplatCount);
            ed.put("deletedCount", popcount(payload));
            m.put("editing", ed);
        } catch (Exception e) {
            Log.w(TAG, "writeMask: 更新 meta 失败 " + e.getMessage());
            return false;
        }
        ProjectStore.writeMeta(c, id, m);
        return true;
    }

    /** 应用 mask + crop 后导出 {projectDir}/edit.ply。失败返回 null。 */
    public static File exportEdit(Context c, String id) {
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            Log.w(TAG, "exportEdit: 项目 id 不合法 " + id);
            return null;
        }
        File src = new File(d, PLY_NAME);
        if (!src.isFile()) {
            Log.w(TAG, "exportEdit: 找不到 " + src);
            return null;
        }
        JSONObject m = ProjectStore.meta(c, id);
        CropParams cp = cropOf(m);
        int rev = revisionOf(m);
        File dst = new File(d, EDIT_PLY_NAME);
        long kept = exportPly(src, dst, new File(d, ".edit-" + rev + ".ply.tmp"),
                readMask(c, id), cp.kind, cp.center, cp.param);
        return kept < 0 ? null : dst;
    }

    /** 同 exportEdit，额外返回 {"ok","path","splats","message"}，供 JS 直接消费。 */
    public static JSONObject exportEditJson(Context c, String id) {
        JSONObject out = new JSONObject();
        File d = ProjectStore.dir(c, id);
        if (d == null) return fail(out, "项目 id 不合法：" + id);
        File src = new File(d, PLY_NAME);
        if (!src.isFile()) return fail(out, "源模型 final.ply 不存在，无法导出");

        JSONObject m = ProjectStore.meta(c, id);
        CropParams cp = cropOf(m);
        File dst = new File(d, EDIT_PLY_NAME);
        long kept = exportPly(src, dst, new File(d, ".edit-" + revisionOf(m) + ".ply.tmp"),
                readMask(c, id), cp.kind, cp.center, cp.param);
        if (kept < 0) return fail(out, "导出失败，详见 logcat（EditStore）");
        try {
            out.put("ok", true);
            out.put("path", dst.getAbsolutePath());
            out.put("splats", kept);
        } catch (Exception ignored) {
        }
        return out;
    }

    /** 从 meta.editing.crop 取出导出要用的裁剪参数；没设或参数不全都退化为「无裁剪」 */
    private static CropParams cropOf(JSONObject meta) {
        JSONObject ed = meta == null ? null : meta.optJSONObject("editing");
        JSONObject crop = ed == null ? null : ed.optJSONObject("crop");
        CropParams cp = new CropParams();
        if (crop == null) return cp;
        String k = crop.optString("kind", "");
        cp.center = dblArray(crop.optJSONArray("center"), 3);
        if ("sphere".equals(k)) {
            cp.kind = CROP_SPHERE;
            cp.param = new double[]{crop.optDouble("radius", 0)};
        } else if ("box".equals(k)) {
            cp.kind = CROP_BOX;
            cp.param = dblArray(crop.optJSONArray("size"), 3);
        }
        if (cp.center == null || cp.param == null) {
            Log.w(TAG, "crop 参数不完整（kind=" + k + "），按无裁剪处理");
            cp.kind = CROP_NONE;
        }
        return cp;
    }

    private static int revisionOf(JSONObject meta) {
        JSONObject ed = meta == null ? null : meta.optJSONObject("editing");
        return ed == null ? 0 : ed.optInt("revision", 0);
    }

    /** crop 参数的裸值形态，方便直接喂给流式导出 */
    private static final class CropParams {
        int kind = CROP_NONE;
        double[] center;
        double[] param;
    }

    /** rotation 是三元素欧拉角，单位度；scale 是标量。 */
    public static boolean saveTransform(Context c, String id, double[] position, double[] rotation,
                                        double scale) {
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            Log.w(TAG, "saveTransform: 项目 id 不合法 " + id);
            return false;
        }
        if (len3(position) || len3(rotation)) {
            Log.w(TAG, "saveTransform: position/rotation 必须是 3 元素数组");
            return false;
        }
        JSONObject m = ProjectStore.meta(c, id);
        if (m == null) m = new JSONObject();
        try {
            JSONObject t = new JSONObject();
            t.put("position", jsonArray(position));
            t.put("rotation", jsonArray(rotation));
            t.put("scale", scale);
            m.put("transform", t);
        } catch (Exception e) {
            Log.w(TAG, "saveTransform: " + e.getMessage());
            return false;
        }
        ProjectStore.writeMeta(c, id, m);
        return true;
    }

    /**
     * 保存裁剪区域。kind 为 null / "sphere" / "box"。
     * param：sphere 时取 [0] 作 radius；box 时取 [0..2] 作 size。
     */
    public static boolean saveCrop(Context c, String id, String kind, double[] center,
                                   double[] param) {
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            Log.w(TAG, "saveCrop: 项目 id 不合法 " + id);
            return false;
        }
        JSONObject m = ProjectStore.meta(c, id);
        if (m == null) m = new JSONObject();
        try {
            JSONObject ed = m.optJSONObject("editing");
            if (ed == null) ed = new JSONObject();
            if (kind == null || kind.isEmpty() || "none".equals(kind)) {
                ed.put("crop", JSONObject.NULL);
            } else if ("sphere".equals(kind)) {
                if (len3(center)) {
                    Log.w(TAG, "saveCrop: sphere 需要 3 元素 center");
                    return false;
                }
                JSONObject cr = new JSONObject();
                cr.put("kind", "sphere");
                cr.put("center", jsonArray(center));
                cr.put("radius", param == null || param.length < 1 ? 0 : param[0]);
                ed.put("crop", cr);
            } else if ("box".equals(kind)) {
                if (len3(center) || len3(param)) {
                    Log.w(TAG, "saveCrop: box 需要 3 元素 center 与 size");
                    return false;
                }
                JSONObject cr = new JSONObject();
                cr.put("kind", "box");
                cr.put("center", jsonArray(center));
                cr.put("size", jsonArray(param));
                ed.put("crop", cr);
            } else {
                Log.w(TAG, "saveCrop: 未知 crop kind " + kind);
                return false;
            }
            m.put("editing", ed);
        } catch (Exception e) {
            Log.w(TAG, "saveCrop: " + e.getMessage());
            return false;
        }
        ProjectStore.writeMeta(c, id, m);
        return true;
    }

    /** 清空全部编辑：删 mask、editing 归零、transform 归默认、删 edit.ply。 */
    public static boolean reset(Context c, String id) {
        File d = ProjectStore.dir(c, id);
        if (d == null) {
            Log.w(TAG, "reset: 项目 id 不合法 " + id);
            return false;
        }
        File edits = new File(d, EDITS_DIR);
        File[] fs = edits.listFiles();
        if (fs != null) {
            for (File f : fs) {
                if (f.getName().startsWith(MASK_PREFIX) && f.getName().endsWith(MASK_SUFFIX)) {
                    f.delete();
                }
            }
        }
        File ply = new File(d, EDIT_PLY_NAME);
        if (ply.exists()) ply.delete();

        JSONObject m = ProjectStore.meta(c, id);
        if (m == null) m = new JSONObject();
        try {
            JSONObject ed = new JSONObject();
            ed.put("crop", JSONObject.NULL);
            ed.put("revision", 0);
            ed.put("sourceSplatCount", 0);
            ed.put("deletedCount", 0);
            m.put("editing", ed);
            m.put("transform", defaultTransform());
        } catch (Exception e) {
            Log.w(TAG, "reset: " + e.getMessage());
            return false;
        }
        ProjectStore.writeMeta(c, id, m);
        return true;
    }

    // ==================== 纯数据层（不依赖 Context，可离线验证） ====================

    /**
     * 解析单个 mask 文件，返回 payload。
     * 必须过四道校验：magic、version==1、maskBytes==payload.length、checksum 匹配，任一不符返回 null。
     */
    public static byte[] readMaskFile(File f) {
        if (f == null || !f.isFile()) return null;
        byte[] all;
        try {
            all = java.nio.file.Files.readAllBytes(f.toPath());
        } catch (IOException e) {
            Log.w(TAG, "readMaskFile: 读取失败 " + f + " " + e.getMessage());
            return null;
        }
        if (all.length < HEADER_BYTES) {
            Log.w(TAG, "readMaskFile: 文件过短 " + f + " (" + all.length + "B < 52B)");
            return null;
        }
        for (int i = 0; i < MAGIC.length; i++) {
            if (all[i] != MAGIC[i]) {
                Log.w(TAG, "readMaskFile: magic 不是 OOSMASK\\0 " + f);
                return null;
            }
        }
        ByteBuffer bb = ByteBuffer.wrap(all).order(ByteOrder.LITTLE_ENDIAN);
        int version = bb.getInt(8);
        if (version != MASK_VERSION) {
            Log.w(TAG, "readMaskFile: version=" + version + " 不是 1 " + f);
            return null;
        }
        long maskBytes = bb.getLong(0x24);
        long checksum = bb.getLong(0x2C);
        if (maskBytes != all.length - HEADER_BYTES) {
            Log.w(TAG, "readMaskFile: maskBytes=" + maskBytes + " 与实际 "
                    + (all.length - HEADER_BYTES) + " 不符 " + f);
            return null;
        }
        byte[] payload = new byte[(int) maskBytes];
        System.arraycopy(all, HEADER_BYTES, payload, 0, payload.length);
        long actual = fnv1a64(payload);
        if (actual != checksum) {
            Log.w(TAG, "readMaskFile: checksum 不符 " + f + " 期望 "
                    + hex(checksum) + " 实际 " + hex(actual));
            return null;
        }
        return payload;
    }

    /**
     * 组装完整 mask 文件字节（52 头 + payload）。
     * 校验 payload.length == ceil(sourceSplatCount/8)，且最后一个字节里越界的位必须为 0。
     */
    public static byte[] buildMaskFile(byte[] payload, int revision, long sourceSplatCount) {
        if (payload == null) {
            Log.w(TAG, "buildMaskFile: payload 为 null");
            return null;
        }
        if (sourceSplatCount <= 0) {
            Log.w(TAG, "buildMaskFile: sourceSplatCount 必须 > 0，实际 " + sourceSplatCount);
            return null;
        }
        long expect = (sourceSplatCount + 7L) / 8L;
        if (payload.length != expect) {
            Log.w(TAG, "buildMaskFile: payload 长度 " + payload.length + " != ceil("
                    + sourceSplatCount + "/8)=" + expect);
            return null;
        }
        // 尾部越界位必须为 0，否则位图与 splat 数不一致
        int tail = (int) (expect * 8L - sourceSplatCount);
        if (tail > 0) {
            int last = payload[payload.length - 1] & 0xFF;
            int mask = (0xFF << (8 - tail)) & 0xFF;
            if ((last & mask) != 0) {
                Log.w(TAG, "buildMaskFile: 尾部越界位被置位（tailBits=" + tail + " lastByte=0x"
                        + Integer.toHexString(last) + "）");
                return null;
            }
        }
        byte[] out = new byte[HEADER_BYTES + payload.length];
        ByteBuffer bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN);
        bb.put(MAGIC);
        bb.putInt(MASK_VERSION);
        bb.putInt(revision);
        bb.putInt(0);                                   // 保留字段
        bb.putLong(sourceSplatCount);
        bb.putLong(popcount(payload));
        bb.putLong(payload.length);
        bb.putLong(fnv1a64(payload));
        System.arraycopy(payload, 0, out, HEADER_BYTES, payload.length);
        return out;
    }

    /** FNV-1a-64，桌面版同款：offset basis 0xcbf29ce484222325，prime 0x100000001b3 */
    public static long fnv1a64(byte[] p) {
        long h = 0xcbf29ce484222325L;
        for (int i = 0; i < p.length; i++) {
            h ^= (p[i] & 0xFF);
            h *= 0x100000001b3L;
        }
        return h;
    }

    /** 位图里 1 的个数 = deletedCount */
    public static long popcount(byte[] p) {
        long n = 0;
        int i = 0;
        for (; i + 8 <= p.length; i += 8) {
            long v = 0;
            for (int j = 7; j >= 0; j--) v = (v << 8) | (p[i + j] & 0xFF);
            n += Long.bitCount(v);
        }
        for (; i < p.length; i++) n += Integer.bitCount(p[i] & 0xFF);
        return n;
    }

    /** LSB-first：bit i = payload[i>>3] >> (i&7) & 1 */
    static boolean bitSet(byte[] payload, long i) {
        long byteIdx = i >> 3;
        if (payload == null || byteIdx < 0 || byteIdx >= payload.length) return false;
        return ((payload[(int) byteIdx] >> (int) (i & 7)) & 1) != 0;
    }

    /**
     * crop 区域判定，与桌面 shader 的 ooosplatCropContains 完全一致：区域内 = 保留。
     * kind 0 全保留 / 1 球（distance <= radius）/ 2 盒（|d| <= size*0.5）。
     */
    static boolean cropContains(double x, double y, double z, int kind, double[] center,
                                double[] param) {
        if (kind == CROP_NONE || center == null || center.length < 3) return true;
        double dx = x - center[0];
        double dy = y - center[1];
        double dz = z - center[2];
        if (kind == CROP_SPHERE) {
            if (param == null || param.length < 1) return true;
            return Math.sqrt(dx * dx + dy * dy + dz * dz) <= param[0];
        }
        if (kind == CROP_BOX) {
            if (param == null || param.length < 3) return true;
            return Math.abs(dx) <= param[0] * 0.5
                    && Math.abs(dy) <= param[1] * 0.5
                    && Math.abs(dz) <= param[2] * 0.5;
        }
        return true;
    }

    /**
     * 流式导出：src → tmp → rename dst。返回保留顶点数，失败返回 -1。
     * 绝不整读进内存：header 之后逐顶点读 stride 字节，保留的原样写出。
     */
    static long exportPly(File src, File dst, File tmp, byte[] mask, int cropKind,
                          double[] cropCenter, double[] cropParam) {
        PlyHeader h;
        try {
            h = PlyHeader.parse(src);
        } catch (IOException e) {
            Log.w(TAG, "exportPly: 解析 PLY 头失败 " + e.getMessage());
            return -1;
        }
        if (h == null) return -1;
        if (!h.binaryLittleEndian) {
            Log.w(TAG, "exportPly: 只支持 binary_little_endian 1.0，实际 format=" + h.format);
            return -1;
        }
        if (h.vertexCount <= 0 || h.stride <= 0) {
            Log.w(TAG, "exportPly: PLY 头异常 vertex=" + h.vertexCount + " stride=" + h.stride);
            return -1;
        }
        if (cropKind != CROP_NONE && (h.offX < 0 || h.offY < 0 || h.offZ < 0)) {
            Log.w(TAG, "exportPly: PLY 缺少 x/y/z property，无法按裁剪区域过滤");
            return -1;
        }

        // 第一遍只数个数（header 里必须先知道保留数），第二遍才真正写文件。
        // 两遍都用同一套流式过滤，绝不整读进内存。
        long kept;
        try {
            kept = filterVertices(src, h, null, mask, cropKind, cropCenter, cropParam);
        } catch (IOException e) {
            Log.w(TAG, "exportPly: 统计失败 " + e.getMessage());
            return -1;
        }
        if (kept < 0) return -1;

        BufferedOutputStream out = null;
        boolean ok = false;
        try {
            out = new BufferedOutputStream(new FileOutputStream(tmp), 1 << 20);
            byte[] hdr = PlyHeader.withVertexCount(h.raw, kept);
            out.write(hdr);
            long written = filterVertices(src, h, out, mask, cropKind, cropCenter, cropParam);
            if (written != kept) {
                Log.w(TAG, "exportPly: 写入数 " + written + " 与统计数 " + kept + " 不一致");
                return -1;
            }
            out.flush();
            ok = true;
        } catch (IOException e) {
            Log.w(TAG, "exportPly: IO 失败 " + e.getMessage());
            return -1;
        } finally {
            closeQuietly(out);
            if (!ok) tmp.delete();
        }
        if (!atomicReplace(tmp, dst)) {
            Log.w(TAG, "exportPly: rename 失败 " + dst);
            return -1;
        }
        return kept;
    }

    /**
     * 逐顶点流式过滤：out 为 null 时只统计不写，否则把保留顶点原样写出。
     * 返回保留数，数据截断返回 -1。
     */
    private static long filterVertices(File src, PlyHeader h, BufferedOutputStream out,
                                       byte[] mask, int cropKind, double[] cropCenter,
                                       double[] cropParam) throws IOException {
        BufferedInputStream in = new BufferedInputStream(new FileInputStream(src), 1 << 20);
        try {
            long skipped = 0;
            while (skipped < h.headerLength) {
                long s = in.skip(h.headerLength - skipped);
                if (s <= 0) break;
                skipped += s;
            }
            byte[] vtx = new byte[h.stride];
            ByteBuffer bb = ByteBuffer.wrap(vtx).order(ByteOrder.LITTLE_ENDIAN);
            boolean needXyz = cropKind != CROP_NONE;
            long kept = 0;
            for (long i = 0; i < h.vertexCount; i++) {
                if (!readFully(in, vtx)) {
                    Log.w(TAG, "exportPly: 顶点数据在第 " + i + " 个处截断（期望 "
                            + h.vertexCount + "）");
                    return -1;
                }
                if (bitSet(mask, i)) continue;
                if (needXyz && !cropContains(bb.getFloat(h.offX), bb.getFloat(h.offY),
                        bb.getFloat(h.offZ), cropKind, cropCenter, cropParam)) {
                    continue;
                }
                if (out != null) out.write(vtx);
                kept++;
            }
            return kept;
        } finally {
            closeQuietly(in);
        }
    }

    // ==================== PLY header ====================

    /** 只解析导出需要的东西：format、vertex 数、stride、x/y/z 偏移、header 原始字节 */
    static final class PlyHeader {
        byte[] raw;
        String format = "";
        boolean binaryLittleEndian;
        long vertexCount;
        int stride;
        int offX = -1;
        int offY = -1;
        int offZ = -1;
        int headerLength;

        static PlyHeader parse(File f) throws IOException {
            byte[] raw = readHeaderBytes(f);
            PlyHeader h = new PlyHeader();
            h.raw = raw;
            h.headerLength = raw.length;
            String[] lines = new String(raw, StandardCharsets.US_ASCII).split("\n");
            boolean inVertex = false;
            for (String line : lines) {
                String t = line.trim();
                if (t.startsWith("format ")) {
                    h.format = t.substring(7).trim();
                } else if (t.startsWith("element ")) {
                    String[] p = t.split("\\s+");
                    inVertex = p.length >= 3 && "vertex".equals(p[1]);
                    if (inVertex) h.vertexCount = Long.parseLong(p[2]);
                } else if (t.startsWith("property ") && inVertex) {
                    String[] p = t.split("\\s+");
                    if (p.length < 3) continue;
                    if ("list".equals(p[1])) {
                        throw new IOException("不支持 list 类型 property：" + t);
                    }
                    int size = typeSize(p[1]);
                    if (size <= 0) throw new IOException("未知 property 类型：" + p[1]);
                    String name = p[p.length - 1];
                    if ("x".equals(name)) h.offX = h.stride;
                    else if ("y".equals(name)) h.offY = h.stride;
                    else if ("z".equals(name)) h.offZ = h.stride;
                    h.stride += size;
                }
            }
            h.binaryLittleEndian = "binary_little_endian 1.0".equals(h.format);
            return h;
        }

        /** 只替换 element vertex 后的数字，其余字节一字不动 */
        static byte[] withVertexCount(byte[] raw, long count) {
            byte[] pre = "element vertex ".getBytes(StandardCharsets.US_ASCII);
            int idx = indexOf(raw, pre);
            if (idx < 0) return raw;
            int p = idx + pre.length;
            int q = p;
            while (q < raw.length && raw[q] >= '0' && raw[q] <= '9') q++;
            byte[] rep = ("element vertex " + count).getBytes(StandardCharsets.US_ASCII);
            byte[] out = new byte[raw.length - (q - idx) + rep.length];
            System.arraycopy(raw, 0, out, 0, idx);
            System.arraycopy(rep, 0, out, idx, rep.length);
            System.arraycopy(raw, q, out, idx + rep.length, raw.length - q);
            return out;
        }

        private static int typeSize(String t) {
            for (int i = 0; i < SCALAR_TYPES.length; i++) {
                if (SCALAR_TYPES[i].equals(t)) return SCALAR_SIZES[i];
            }
            return -1;
        }

        /** 读到 end_header 所在行的换行符为止，返回完整 header 字节 */
        private static byte[] readHeaderBytes(File f) throws IOException {
            FileInputStream fis = new FileInputStream(f);
            try {
                byte[] acc = new byte[8192];
                int n = 0;
                while (true) {
                    int red = fis.read(acc, n, acc.length - n);
                    if (red < 0) throw new IOException("PLY 头里没有 end_header：" + f);
                    n += red;
                    int e = indexOf(acc, n, END_HEADER);
                    if (e >= 0) {
                        int end = e;
                        while (end < n && acc[end] != '\n') end++;
                        if (end < n) end++;            // 连同换行一起算进 header
                        byte[] out = new byte[end];
                        System.arraycopy(acc, 0, out, 0, end);
                        return out;
                    }
                    if (n == acc.length) {
                        if (acc.length >= (1 << 20)) {
                            throw new IOException("PLY 头超过 1MB，格式异常：" + f);
                        }
                        byte[] nb = new byte[acc.length * 2];
                        System.arraycopy(acc, 0, nb, 0, n);
                        acc = nb;
                    }
                }
            } finally {
                fis.close();
            }
        }
    }

    // ==================== 小工具 ====================

    private static File latestMask(File projectDir) {
        File edits = new File(projectDir, EDITS_DIR);
        File[] fs = edits.listFiles();
        if (fs == null) return null;
        File best = null;
        int bestRev = -1;
        for (File f : fs) {
            String n = f.getName();
            if (!f.isFile() || !n.startsWith(MASK_PREFIX) || !n.endsWith(MASK_SUFFIX)) continue;
            String mid = n.substring(MASK_PREFIX.length(), n.length() - MASK_SUFFIX.length());
            int rev;
            try {
                rev = Integer.parseInt(mid);
            } catch (NumberFormatException e) {
                continue;
            }
            if (rev > bestRev) {
                bestRev = rev;
                best = f;
            }
        }
        return best;
    }

    /** rename 失败时退化为 delete + rename，保证最终 dst 是完整新文件 */
    private static boolean atomicReplace(File tmp, File dst) {
        if (tmp == null || !tmp.exists()) return false;
        if (tmp.renameTo(dst)) return true;
        if (dst.exists() && !dst.delete()) return false;
        return tmp.renameTo(dst);
    }

    private static boolean readFully(java.io.InputStream in, byte[] b) throws IOException {
        int n = 0;
        while (n < b.length) {
            int r = in.read(b, n, b.length - n);
            if (r < 0) return false;
            n += r;
        }
        return true;
    }

    private static void closeQuietly(java.io.Closeable c) {
        if (c == null) return;
        try {
            c.close();
        } catch (IOException ignored) {
        }
    }

    private static int indexOf(byte[] hay, byte[] needle) {
        return indexOf(hay, hay.length, needle);
    }

    private static int indexOf(byte[] hay, int limit, byte[] needle) {
        int last = limit - needle.length;
        for (int i = 0; i <= last; i++) {
            boolean ok = true;
            for (int j = 0; j < needle.length; j++) {
                if (hay[i + j] != needle[j]) {
                    ok = false;
                    break;
                }
            }
            if (ok) return i;
        }
        return -1;
    }

    private static JSONObject defaultTransform() throws Exception {
        JSONObject t = new JSONObject();
        t.put("position", jsonArray(new double[]{0, 0, 0}));
        t.put("rotation", jsonArray(new double[]{0, 0, 0}));
        t.put("scale", 1.0);
        return t;
    }

    private static JSONObject transformOf(JSONObject m) {
        JSONObject t = m == null ? null : m.optJSONObject("transform");
        if (t == null) {
            try {
                return defaultTransform();
            } catch (Exception e) {
                return new JSONObject();
            }
        }
        return t;
    }

    private static JSONArray jsonArray(double[] v) {
        JSONArray a = new JSONArray();
        if (v == null) return a;
        // 走 put(Object) 重载，避开 put(double) 的受检 JSONException
        for (double d : v) a.put(Double.valueOf(d));
        return a;
    }

    private static double[] dblArray(JSONArray a, int n) {
        if (a == null || a.length() < n) return null;
        double[] v = new double[n];
        for (int i = 0; i < n; i++) v[i] = a.optDouble(i, 0);
        return v;
    }

    /** 不是正好 3 元素就为 true（命名上是「长度不合法」） */
    private static boolean len3(double[] v) {
        return v == null || v.length != 3;
    }

    private static JSONObject fail(JSONObject out, String msg) {
        try {
            out.put("ok", false);
            out.put("message", msg);
        } catch (Exception ignored) {
        }
        return out;
    }

    private static String hex(long v) {
        return String.format("%016x", v);
    }
}
