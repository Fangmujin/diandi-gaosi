package com.diandi.gaosi;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

/**
 * 抽帧：MediaMetadataRetriever 按等间隔取帧（HEVC/AVC 由系统解码器兜底，仅做兼容层）。
 * ANR 防护：本类只在 PipelineService 的后台线程调用，且每帧之后显式让出 + 回调上报。
 */
public final class FrameExtractor {

    private static final String TAG = "FrameExtractor";

    public interface Progress {
        /** 0..1 */
        void onProgress(int done, int total, String detail);

        boolean cancelled();
    }

    private FrameExtractor() {
    }

    /** 素材时长（毫秒）；读不到返回 -1（图片素材）。 */
    public static long duration(Context c, Uri uri) {
        MediaMetadataRetriever mmr = new MediaMetadataRetriever();
        try {
            mmr.setDataSource(c, uri);
            String ds = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            if (ds != null) return Long.parseLong(ds);
        } catch (Exception e) {
            Log.w(TAG, "duration failed: " + e.getMessage());
        } finally {
            try {
                mmr.release();
            } catch (Exception ignored) {
            }
        }
        return -1;
    }

    public static int extract(Context c, Uri uri, File outDir, int count, int maxSize, Progress cb)
            throws Exception {
        if (!outDir.exists()) outDir.mkdirs();
        for (File f : outDir.listFiles()) f.delete();

        MediaMetadataRetriever mmr = new MediaMetadataRetriever();
        long durationMs = -1;
        try {
            mmr.setDataSource(c, uri);
            String ds = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            if (ds != null) durationMs = Long.parseLong(ds);
        } catch (Exception e) {
            Log.w(TAG, "retriever setDataSource failed: " + e.getMessage());
            durationMs = -1;
        }

        try {
            if (durationMs <= 0) {
                return extractStill(c, uri, outDir, maxSize, cb);
            }
            // 视频：≤60s 素材，等间隔抽帧，首尾各留 4% 余量避开黑场/转场
            long start = (long) (durationMs * 0.04);
            long end = (long) (durationMs * 0.96);
            long span = Math.max(1, end - start);
            int written = 0;
            for (int i = 0; i < count; i++) {
                if (cb != null && cb.cancelled()) break;
                long t = start + (span * i) / Math.max(1, count - 1) * (count > 1 ? 1 : 0);
                if (count > 1) t = start + (span * i) / (count - 1);
                Bitmap b = null;
                try {
                    b = mmr.getFrameAtTime(t * 1000L, MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
                } catch (Exception e) {
                    Log.w(TAG, "frame " + i + " failed: " + e.getMessage());
                }
                if (b == null) continue;
                written += write(b, new File(outDir, String.format("frame_%04d.jpg", written)), maxSize);
                if (cb != null) {
                    cb.onProgress(written, count, "抽帧 " + written + "/" + count);
                }
                Thread.yield();
            }
            return written;
        } finally {
            try {
                mmr.release();
            } catch (Exception ignored) {
            }
        }
    }

    /** 单张照片：直接作为唯一一帧 */
    private static int extractStill(Context c, Uri uri, File outDir, int maxSize, Progress cb)
            throws Exception {
        InputStream in = c.getContentResolver().openInputStream(uri);
        if (in == null) throw new Exception("无法读取所选文件");
        Bitmap b = BitmapFactory.decodeStream(in);
        try {
            in.close();
        } catch (Exception ignored) {
        }
        if (b == null) throw new Exception("所选文件不是可解码的图片或视频");
        int n = write(b, new File(outDir, "frame_0000.jpg"), maxSize);
        if (cb != null) cb.onProgress(n, n, "已取 1 帧");
        return n;
    }

    private static int write(Bitmap src, File out, int maxSize) {
        Bitmap b = src;
        int w = b.getWidth(), h = b.getHeight();
        int m = Math.max(w, h);
        if (maxSize > 0 && m > maxSize) {
            float s = (float) maxSize / (float) m;
            int nw = Math.max(1, Math.round(w * s));
            int nh = Math.max(1, Math.round(h * s));
            Bitmap scaled = Bitmap.createScaledBitmap(b, nw, nh, true);
            if (scaled != b) b.recycle();
            b = scaled;
        }
        try {
            FileOutputStream os = new FileOutputStream(out);
            b.compress(Bitmap.CompressFormat.JPEG, 92, os);
            os.close();
        } catch (Exception e) {
            Log.w(TAG, "write frame failed: " + e.getMessage());
            return 0;
        } finally {
            if (b != src) b.recycle();
            src.recycle();
        }
        return 1;
    }
}
