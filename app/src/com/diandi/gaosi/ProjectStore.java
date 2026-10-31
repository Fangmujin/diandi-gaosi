package com.diandi.gaosi;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * 图库池：filesDir/projects/<id>/ 目录 + index.json 索引。
 * 每个项目目录内：meta.json、cover.jpg、frames/、dataset/(images + sparse/0)、final.ply
 */
public final class ProjectStore {

    private static final String IDX = "index.json";
    private static final SimpleDateFormat SDF =
            new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA);

    /** 合法项目 id：只允许字母数字下划线短横，杜绝 "" / "." / ".." / 路径分隔符 */
    private static final Pattern SAFE_ID = Pattern.compile("[A-Za-z0-9_-]{1,64}");

    /** 中间产物目录：失败/中断项目的空间大头，清理时整目录删除 */
    private static final String[] INTERMEDIATES = {"frames", "dataset", "colmap", "tmp"};

    /** status 仍是 running 但超过这个时长没有活跃任务，判定为进程已死 */
    private static final long STALE_MS = 30L * 60L * 1000L;

    private ProjectStore() {
    }

    /**
     * id 合法性校验。任何进入文件系统路径的 id 都必须先过这一关。
     * 空字符串 / 含路径分隔符 / ".." 一律拒，避免退化到 projects 根目录。
     */
    private static boolean isSafeId(String id) {
        return id != null && SAFE_ID.matcher(id).matches();
    }

    /**
     * 删除前的最后一道闸：目标必须是 projects 根目录的<b>直接子目录</b>，
     * 且不能是根目录本身。用规范路径比较，穿透符号链接与 ".."。
     */
    private static boolean isDirectChildOfRoot(Context c, File target) {
        if (target == null) return false;
        try {
            String rp = root(c).getCanonicalPath();
            String tp = target.getCanonicalPath();
            if (!tp.startsWith(rp + File.separator)) return false;
            return !tp.equals(rp);
        } catch (Exception e) {
            return false;
        }
    }

    public static File root(Context c) {
        File d = new File(c.getFilesDir(), "projects");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    /** 非法 id 一律返回 null，绝不退化到根目录 */
    public static File dir(Context c, String id) {
        if (!isSafeId(id)) return null;
        return new File(root(c), id);
    }

    private static File idxFile(Context c) {
        return new File(root(c), IDX);
    }

    public static String newId() {
        return "p" + System.currentTimeMillis();
    }

    private static synchronized JSONArray readIndex(Context c) {
        File f = idxFile(c);
        if (!f.exists()) return new JSONArray();
        try {
            byte[] b = java.nio.file.Files.readAllBytes(f.toPath());
            return new JSONArray(new String(b, StandardCharsets.UTF_8));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static synchronized void writeIndex(Context c, JSONArray arr) {
        try {
            java.nio.file.Files.write(idxFile(c).toPath(),
                    arr.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception ignored) {
        }
    }

    public static synchronized JSONObject create(Context c, String name, String quality) {
        String id = newId();
        File d = dir(c, id);
        d.mkdirs();
        new File(d, "frames").mkdirs();
        JSONObject meta = new JSONObject();
        try {
            meta.put("id", id);
            meta.put("name", name);
            meta.put("quality", quality);
            meta.put("status", "running");   // running | done | failed
            meta.put("progress", 0);
            meta.put("stage", "准备中");
            meta.put("createdAt", System.currentTimeMillis());
            meta.put("date", SDF.format(new Date()));
            meta.put("splats", 0);
            meta.put("bytes", 0);
            meta.put("frames", 0);
            meta.put("error", "");
        } catch (Exception ignored) {
        }
        writeMeta(c, id, meta);

        JSONArray arr = readIndex(c);
        arr.put(meta);
        writeIndex(c, arr);
        return meta;
    }

    /** 重试：复用同一项目，清空中间产物 */
    public static synchronized JSONObject reset(Context c, String id, String name, String quality) {
        JSONObject m = meta(c, id);
        if (m == null) return create(c, name, quality);
        try {
            m.put("name", name);
            m.put("quality", quality);
            m.put("status", "running");
            m.put("progress", 0);
            m.put("stage", "准备中");
            m.put("splats", 0);
            m.put("bytes", 0);
            m.put("frames", 0);
            m.remove("error");
        } catch (Exception ignored) {
        }
        File d = dir(c, id);
        rmrf(new File(d, "frames"));
        rmrf(new File(d, "colmap"));
        rmrf(new File(d, "dataset"));
        rmrf(new File(d, "tmp"));
        File ply = new File(d, "final.ply");
        if (ply.exists()) ply.delete();
        new File(d, "frames").mkdirs();
        writeMeta(c, id, m);
        return m;
    }

    public static synchronized void writeMeta(Context c, String id, JSONObject meta) {
        if (!isSafeId(id)) return;
        try {
            java.nio.file.Files.write(new File(dir(c, id), "meta.json").toPath(),
                    meta.toString().getBytes(StandardCharsets.UTF_8));
            // 同步进索引
            JSONArray arr = readIndex(c);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o != null && id.equals(o.optString("id"))) {
                    arr.put(i, meta);
                    writeIndex(c, arr);
                    return;
                }
            }
            arr.put(meta);
            writeIndex(c, arr);
        } catch (Exception ignored) {
        }
    }

    public static JSONObject meta(Context c, String id) {
        File d = dir(c, id);
        if (d == null) return null;
        File f = new File(d, "meta.json");
        if (!f.exists()) return null;
        try {
            return new JSONObject(new String(java.nio.file.Files.readAllBytes(f.toPath()),
                    StandardCharsets.UTF_8));
        } catch (Exception e) {
            return null;
        }
    }

    /** 图库页：最近在上 */
    public static synchronized JSONArray list(Context c) {
        JSONArray arr = readIndex(c);
        List<JSONObject> items = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o != null) items.add(o);
        }
        java.util.Collections.sort(items, new java.util.Comparator<JSONObject>() {
            @Override
            public int compare(JSONObject a, JSONObject b) {
                return Long.compare(b.optLong("createdAt"), a.optLong("createdAt"));
            }
        });
        // 补齐实时状态（模型文件是否真的落地）
        for (JSONObject o : items) {
            File d = dir(c, o.optString("id"));
            if (d == null) continue;   // id 不合法：不碰它，也绝不退化到根目录
            File ply = new File(d, "final.ply");
            // 模型不存在时必须把 bytes 归零，否则会残留上一次的旧大小
            try {
                o.put("bytes", ply.exists() ? ply.length() : 0L);
            } catch (Exception ignored) {
            }
        }
        return new JSONArray(items);
    }

    public static synchronized boolean rename(Context c, String id, String name) {
        if (!isSafeId(id)) return false;
        JSONObject m = meta(c, id);
        if (m == null) return false;
        try {
            m.put("name", name);
        } catch (Exception ignored) {
        }
        writeMeta(c, id, m);
        return true;
    }

    /**
     * 删除单个作品。三重防护保证「只删这一个」：
     * 1) isSafeId —— 空串/含分隔符的 id 直接拒绝；
     * 2) isDirectChildOfRoot —— 规范路径必须是 projects 的直接子目录且不等于根目录；
     * 3) rmrf 只作用于这一个目录，不动兄弟目录。
     */
    public static synchronized boolean delete(Context c, String id) {
        if (!isSafeId(id)) return false;
        File d = dir(c, id);
        if (!isDirectChildOfRoot(c, d)) return false;
        rmrf(d);
        JSONArray arr = readIndex(c);
        JSONArray out = new JSONArray();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o != null && !id.equals(o.optString("id"))) out.put(o);
        }
        writeIndex(c, out);
        return true;
    }

    /**
     * 自动清扫：失败/中断的项目会留下 frames/、dataset/、colmap/、tmp/ 这堆中间产物，
     * 它们是空间大头且永久堆积。这里回收它们，同时保留 meta.json、封面、
     * source 素材副本和已生成的 final.ply（用户还能看到卡片、还能重试）。
     *
     * 返回 {"scanned":N,"cleaned":M,"freedBytes":B}
     */
    public static synchronized JSONObject sweep(Context c) {
        JSONObject out = new JSONObject();
        int scanned = 0;
        int cleaned = 0;
        long freed = 0;
        try {
            String activeId = activeProjectId();
            JSONArray arr = readIndex(c);
            JSONArray keep = new JSONArray();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o == null) continue;
                scanned++;
                String id = o.optString("id");
                String status = o.optString("status", "");
                boolean flipped = false;
                boolean dead;
                if ("failed".equals(status)) {
                    dead = true;
                } else if ("running".equals(status) && !id.equals(activeId) && isStale(o)) {
                    // 没有活跃任务在跑它，且已经过了很久还是 running —— 进程早死了
                    dead = true;
                    flipped = true;
                    try {
                        o.put("status", "failed");
                        o.put("progress", 0);
                        o.put("stage", "已中断");
                        o.put("error", "上次重建被中断（进程已退出），中间产物已自动清理，可直接重试");
                    } catch (Exception ignored) {
                    }
                } else {
                    dead = false;
                }
                if (dead) {
                    long got = cleanIntermediates(c, id);
                    freed += got;
                    if (got > 0 || flipped) cleaned++;
                }
                keep.put(o);
            }
            writeIndex(c, keep);
        } catch (Exception ignored) {
        }
        try {
            out.put("scanned", scanned);
            out.put("cleaned", cleaned);
            out.put("freedBytes", freed);
        } catch (Exception ignored) {
        }
        return out;
    }

    /** 当前真正有任务在跑的项目 id（来自前台服务推的进度），没有就返回 null */
    private static String activeProjectId() {
        try {
            JSONObject p = new JSONObject(AppState.getProgress());
            if (!p.optBoolean("active", false)) return null;
            String id = p.optString("projectId", "");
            return id.isEmpty() ? null : id;
        } catch (Exception e) {
            return null;
        }
    }

    /** running 但时间戳已过期（或缺失时间戳时保守放行，不当成死亡） */
    private static boolean isStale(JSONObject o) {
        long t = o.optLong("updatedAt", 0);
        if (t <= 0) t = o.optLong("createdAt", 0);
        if (t <= 0) return false;
        return System.currentTimeMillis() - t > STALE_MS;
    }

    /** 删掉一个项目的中间产物目录，返回实际释放的字节数 */
    private static long cleanIntermediates(Context c, String id) {
        File d = dir(c, id);
        if (d == null || !isDirectChildOfRoot(c, d) || !d.isDirectory()) return 0;
        long freed = 0;
        for (String name : INTERMEDIATES) {
            File sub = new File(d, name);
            if (!sub.exists()) continue;
            freed += sizeOf(sub);
            rmrf(sub);
        }
        return freed;
    }

    private static long sizeOf(File f) {
        if (f == null || !f.exists()) return 0;
        if (f.isDirectory()) {
            long s = 0;
            File[] cs = f.listFiles();
            if (cs != null) for (File c : cs) s += sizeOf(c);
            return s;
        }
        return f.length();
    }

    private static void rmrf(File f) {
        if (f == null || !f.exists()) return;
        if (f.isDirectory()) {
            File[] cs = f.listFiles();
            if (cs != null) for (File c : cs) rmrf(c);
        }
        f.delete();
    }

    public static void writeText(File f, String s) {
        try {
            FileOutputStream os = new FileOutputStream(f);
            os.write(s.getBytes(StandardCharsets.UTF_8));
            os.close();
        } catch (Exception ignored) {
        }
    }
}
