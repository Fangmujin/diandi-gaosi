package com.diandi.gaosi;

import android.content.Context;
import android.os.Build;

import org.json.JSONObject;

import java.io.File;

/**
 * 探测原生引擎是否就位。引擎由另外两条线交叉编译产出到 native/prebuilt/<abi>/，
 * 构建时随 APK 的 jniLibs 一起打进 lib/<abi>/。缺失时 APK 必须能正常启动并给出友好提示。
 */
public final class EngineProbe {

    public static final String SFM_LIB = "libiii_sfm.so";
    public static final String TRAIN_LIB = "libbrushheadless.so";

    private EngineProbe() {
    }

    public static String abi() {
        String[] abis = Build.SUPPORTED_ABIS;
        return (abis != null && abis.length > 0) ? abis[0] : "unknown";
    }

    public static File libDir(Context c) {
        return new File(c.getApplicationInfo().nativeLibraryDir);
    }

    public static File sfmBin(Context c) {
        return new File(libDir(c), SFM_LIB);
    }

    public static File trainBin(Context c) {
        return new File(libDir(c), TRAIN_LIB);
    }

    /** 引擎存在且可执行；某些 ROM 解压后丢 +x，这里尽力补一次权限（owner-only 也够 exec）。 */
    public static boolean ready(File f) {
        if (f == null || !f.exists()) return false;
        if (f.canExecute()) return true;
        try {
            f.setExecutable(true, true);
        } catch (Throwable ignored) {
        }
        return f.canExecute();
    }

    public static JSONObject status(Context c) {
        JSONObject o = new JSONObject();
        try {
            File sfm = sfmBin(c);
            File tr = trainBin(c);
            o.put("abi", abi());
            o.put("libDir", libDir(c).getAbsolutePath());
            o.put("sfm", ready(sfm));
            o.put("sfmPath", sfm.getAbsolutePath());
            o.put("sfm_lib", SFM_LIB);
            o.put("train", ready(tr));
            o.put("trainPath", tr.getAbsolutePath());
            o.put("train_lib", TRAIN_LIB);
            o.put("device", Build.MANUFACTURER + " " + Build.MODEL);
            o.put("sdk", Build.VERSION.SDK_INT);
        } catch (Exception ignored) {
        }
        return o;
    }
}
