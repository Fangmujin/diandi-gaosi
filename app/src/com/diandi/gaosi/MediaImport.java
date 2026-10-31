package com.diandi.gaosi;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

/**
 * 素材导入：把相册/相机返回的 content Uri 落成应用私有文件，避免 URI 授权在进程重启后失效。
 */
public final class MediaImport {

    private static final String TAG = "MediaImport";

    private MediaImport() {
    }

    public static File dir(Context c) {
        File d = new File(c.getFilesDir(), "imports");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    /** 拷贝素材到私有目录，返回描述 JSON；失败返回 null */
    public static JSONObject importUri(Context c, Uri uri) {
        String name = "src_" + System.currentTimeMillis();
        File out = null;
        try {
            InputStream in = c.getContentResolver().openInputStream(uri);
            if (in == null) return null;
            String type = c.getContentResolver().getType(uri);
            String ext = ".mp4";
            String kind = "video";
            if (type != null && type.startsWith("image/")) {
                ext = ".jpg";
                kind = "image";
            }
            name = name + ext;
            out = new File(dir(c), name);
            FileOutputStream os = new FileOutputStream(out);
            byte[] buf = new byte[65536];
            int r;
            long total = 0;
            while ((r = in.read(buf)) != -1) {
                os.write(buf, 0, r);
                total += r;
                if (total > 800L * 1024 * 1024) break; // 硬上限，避免 OOM 级别素材
            }
            os.close();
            in.close();

            long durMs = 0;
            if ("video".equals(kind)) {
                MediaMetadataRetriever mmr = new MediaMetadataRetriever();
                try {
                    mmr.setDataSource(out.getAbsolutePath());
                    String ds = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
                    if (ds != null) durMs = Long.parseLong(ds);
                } catch (Exception e) {
                    Log.w(TAG, "duration probe failed: " + e.getMessage());
                } finally {
                    try {
                        mmr.release();
                    } catch (Exception ignored) {
                    }
                }
            }
            JSONObject o = new JSONObject();
            o.put("rel", name);
            o.put("name", name);
            o.put("kind", kind);
            o.put("bytes", out.length());
            o.put("durationMs", durMs);
            o.put("thumb", "/thumb?p=" + name);
            return o;
        } catch (Exception e) {
            Log.w(TAG, "import failed: " + e.getMessage());
            if (out != null) out.delete();
            return null;
        }
    }

    /** 生成缩略图 jpeg。视频取首帧，图片按 inSampleSize 降采样。 */
    public static byte[] thumb(Context c, File f, int maxPx) {
        if (f == null || !f.isFile()) return null;
        Bitmap b = null;
        String lower = f.getName().toLowerCase();
        if (lower.endsWith(".mp4") || lower.endsWith(".mov") || lower.endsWith(".webm")
                || lower.endsWith(".mkv") || lower.endsWith(".3gp")) {
            MediaMetadataRetriever mmr = new MediaMetadataRetriever();
            try {
                mmr.setDataSource(f.getAbsolutePath());
                Bitmap t = mmr.getFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
                if (t != null) {
                    int m = Math.max(t.getWidth(), t.getHeight());
                    float s = Math.min(1f, (float) maxPx / (float) Math.max(1, m));
                    if (s < 1f) {
                        b = Bitmap.createScaledBitmap(t,
                                Math.max(1, Math.round(t.getWidth() * s)),
                                Math.max(1, Math.round(t.getHeight() * s)), true);
                        t.recycle();
                    } else {
                        b = t;
                    }
                }
            } catch (Exception e) {
                Log.w(TAG, "video thumb failed: " + e.getMessage());
            } finally {
                try {
                    mmr.release();
                } catch (Exception ignored) {
                }
            }
        }
        if (b == null) {
            try {
                BitmapFactory.Options o1 = new BitmapFactory.Options();
                o1.inJustDecodeBounds = true;
                BitmapFactory.decodeFile(f.getAbsolutePath(), o1);
                int w = o1.outWidth, h = o1.outHeight;
                int sample = 1;
                int m = Math.max(w, h);
                while (m / sample > maxPx) sample *= 2;
                BitmapFactory.Options o2 = new BitmapFactory.Options();
                o2.inSampleSize = sample;
                b = BitmapFactory.decodeFile(f.getAbsolutePath(), o2);
            } catch (Exception e) {
                Log.w(TAG, "image thumb failed: " + e.getMessage());
            }
        }
        if (b == null) return null;
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        b.compress(Bitmap.CompressFormat.JPEG, 82, bos);
        b.recycle();
        return bos.toByteArray();
    }
}
