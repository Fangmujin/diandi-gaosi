package com.diandi.gaosi;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.util.Log;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * content:// 文件供给器，专门给「分享 HTML」用。
 *
 * 为什么自己写而不用 androidx 的 FileProvider：本工程没有 gradle，
 * javac 只挂 android.jar（见 build/javac_step.ps1），androidx 的类和
 * android.support 类都不在类路径上，写出来编译不过。同时 Android 7+ 起
 * file:// 不能跨应用传递（FileUriExposedException），分享必须走 content://，
 * 所以这里用标准 ContentProvider 实现等价语义：只暴露 cacheDir/shared/。
 *
 * 安全边界：openFile/query 都是<b>其他应用</b>可以调的入口，所以路径必须做
 * 目录逃逸校验 —— 用规范路径比较，穿透 ".." 与符号链接，只允许 shared/ 的直接子文件。
 */
public final class ShareFileProvider extends ContentProvider {

    private static final String TAG = "ShareFileProvider";

    /** 必须与 AndroidManifest 里 provider 的 authorities 完全一致 */
    public static final String AUTHORITY = "com.diandi.gaosi.files";

    /** 只暴露 cacheDir 下的这个子目录 */
    private static final String SUBDIR = "shared";

    private static final String[] COLUMNS = {"_display_name", "_size"};

    @Override
    public boolean onCreate() {
        return true;
    }

    /** 把 cacheDir/shared/<name> 打包成可授权的 content:// URI */
    public static Uri uriFor(File f) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(SUBDIR)
                .appendPath(f.getName())
                .build();
    }

    /**
     * URI → 文件。任何一步不合法都返回 null（调用方转成 FileNotFoundException）。
     * 校验顺序：scheme/authority → 段数 → 文件名 → 规范路径必须落在 shared/ 内。
     */
    private File resolve(Uri uri) {
        if (uri == null) return null;
        if (!"content".equals(uri.getScheme())) return null;
        if (!AUTHORITY.equals(uri.getAuthority())) return null;
        java.util.List<String> segs = uri.getPathSegments();
        if (segs == null || segs.size() != 2) return null;
        if (!SUBDIR.equals(segs.get(0))) return null;
        String name = segs.get(1);
        // 段里再查一次：URI 解析后仍可能带编码过的分隔符
        if (name.indexOf('/') >= 0 || name.indexOf('\\') >= 0
                || name.equals(".") || name.equals("..")) {
            return null;
        }
        File root = new File(getContext().getCacheDir(), SUBDIR);
        File f = new File(root, name);
        try {
            String rp = root.getCanonicalPath();
            String fp = f.getCanonicalPath();
            if (!fp.startsWith(rp + File.separator)) return null;
        } catch (Exception e) {
            return null;
        }
        return f.isFile() ? f : null;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = resolve(uri);
        if (f == null) return null;
        MatrixCursor c = new MatrixCursor(COLUMNS, 1);
        c.addRow(new Object[]{f.getName(), f.length()});
        return c;
    }

    @Override
    public String getType(Uri uri) {
        return "text/html";
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = resolve(uri);
        if (f == null) {
            Log.w(TAG, "拒绝越界访问: " + uri);
            throw new FileNotFoundException("非法路径: " + uri);
        }
        int m = ParcelFileDescriptor.MODE_READ_ONLY;
        if (mode != null && (mode.contains("w") || mode.contains("t"))) {
            // 只发只读描述符：接收方改不了我们的缓存文件
            m = ParcelFileDescriptor.MODE_READ_ONLY;
        }
        return ParcelFileDescriptor.open(f, m);
    }

    @Override
    public String[] getStreamTypes(Uri uri, String mimeTypeFilter) {
        return new String[]{"text/html"};
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("只读供给器");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("只读供给器");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("只读供给器");
    }
}
