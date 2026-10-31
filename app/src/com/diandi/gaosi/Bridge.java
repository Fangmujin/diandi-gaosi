package com.diandi.gaosi;

import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.util.Base64;
import android.util.Log;
import android.webkit.JavascriptInterface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;

/** WebView JS 桥：window.Diandi.* */
public final class Bridge {

    private static final String TAG = "Bridge";
    private final MainActivity act;

    Bridge(MainActivity act) {
        this.act = act;
    }

    @JavascriptInterface
    public String engineStatus() {
        return EngineProbe.status(act).toString();
    }

    @JavascriptInterface
    public String appInfo() {
        JSONObject o = EngineProbe.status(act);
        try {
            o.put("version", "0.1");
            o.put("filesDir", act.getFilesDir().getAbsolutePath());
            o.put("port", act.httpPort());
            o.put("paramsFast", PipelineManager.paramsFor("fast").toJson());
            o.put("paramsFine", PipelineManager.paramsFor("fine").toJson());
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    @JavascriptInterface
    public void pickMedia() {
        act.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                Intent i = new Intent(Intent.ACTION_GET_CONTENT);
                i.setType("video/*");
                i.addCategory(Intent.CATEGORY_OPENABLE);
                act.startActivityForResult(Intent.createChooser(i, "选择素材"), MainActivity.REQ_PICK);
            }
        });
    }

    @JavascriptInterface
    public void pickImages() {
        act.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                Intent i = new Intent(Intent.ACTION_GET_CONTENT);
                i.setType("image/*");
                i.addCategory(Intent.CATEGORY_OPENABLE);
                act.startActivityForResult(Intent.createChooser(i, "选择素材"), MainActivity.REQ_PICK);
            }
        });
    }

    @JavascriptInterface
    public void captureVideo() {
        act.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                Intent i = new Intent(android.provider.MediaStore.ACTION_VIDEO_CAPTURE);
                i.putExtra(android.provider.MediaStore.EXTRA_DURATION_LIMIT, 60);
                i.putExtra(android.provider.MediaStore.EXTRA_VIDEO_QUALITY, 1);
                try {
                    act.startActivityForResult(i, MainActivity.REQ_CAPTURE);
                } catch (Exception e) {
                    act.pushEvent("{\"type\":\"toast\",\"message\":\"本机没有可用的相机应用\"}");
                }
            }
        });
    }

    /** 前台服务通知权限（Android 13+） */
    @JavascriptInterface
    public void requestNotifyPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            act.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    act.requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 77);
                }
            });
        }
    }

    @JavascriptInterface
    public String pickedMedia() {
        JSONArray a = act.takePending();
        return a.toString();
    }

    /**
     * 启动重建任务。cfg: {"name":"..","quality":"fast|fine","rel":"src_xxx.mp4"}
     * 返回 {"ok":true,"projectId":".."} 或 {"ok":false,"message":".."}
     */
    @JavascriptInterface
    public String startJob(String cfg) {
        JSONObject out = new JSONObject();
        try {
            JSONObject c = new JSONObject(cfg);
            String name = c.optString("name", "未命名重建");
            String quality = c.optString("quality", "fast");
            String rel = c.optString("rel", "");
            File f = new File(MediaImport.dir(act), rel);
            if (!f.isFile()) {
                out.put("ok", false);
                out.put("message", "素材文件不存在，请重新选择");
                return out.toString();
            }
            String id = c.optString("projectId", "");
            JSONObject meta;
            if (!id.isEmpty() && ProjectStore.meta(act, id) != null) {
                meta = ProjectStore.reset(act, id, name, quality);
            } else {
                meta = ProjectStore.create(act, name, quality);
                id = meta.getString("id");
            }
            try {
                meta.put("sourceRel", rel);
                ProjectStore.writeMeta(act, id, meta);
            } catch (Exception ignored) {
            }

            Intent i = new Intent(act, PipelineService.class);
            i.setAction(PipelineService.ACTION_START);
            i.putExtra(PipelineService.EXTRA_PROJECT_ID, id);
            i.putExtra(PipelineService.EXTRA_PROJECT_NAME, name);
            i.putExtra(PipelineService.EXTRA_QUALITY, quality);
            i.putExtra(PipelineService.EXTRA_SOURCE, Uri.fromFile(f).toString());
            if (Build.VERSION.SDK_INT >= 26) {
                act.startForegroundService(i);
            } else {
                act.startService(i);
            }
            out.put("ok", true);
            out.put("projectId", id);
            out.put("meta", meta);
        } catch (Exception e) {
            Log.w(TAG, "startJob failed: " + e.getMessage());
            try {
                out.put("ok", false);
                out.put("message", "启动失败：" + e.getMessage());
            } catch (Exception ignored) {
            }
        }
        return out.toString();
    }

    @JavascriptInterface
    public String jobProgress() {
        return AppState.getProgress();
    }

    @JavascriptInterface
    public void cancelJob() {
        PipelineService.cancel(act);
    }

    /**
     * 分享 HTML：先把 final.ply 打成单文件自包含 HTML，再弹系统分享面板。
     * 返回 {"ok":true,"path":"..."} / {"ok":false,"message":"..."}。
     *
     * 生成是几十 MB 量级的 base64 编码，**必须在后台线程跑**：这里立刻返回，
     * 主线程只负责 toast 和最后弹面板。把 build 放在主线程会让 UI 冻住十几秒。
     */
    /**
     * 保存 HTML 到手机「下载/点点高斯/」。生成 + 拷贝是耗时操作，放后台线程。
     * API 29+ 走 MediaStore 无需权限；API 24-28 需要 WRITE_EXTERNAL_STORAGE，
     * 缺权限时先申请并回执 pending，用户允许后重试即可。
     */
    @JavascriptInterface
    public String saveHtml(final String id) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
                && act.checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            act.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    act.requestPermissions(
                            new String[]{android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, 78);
                }
            });
            return "{\"ok\":false,\"pending\":true,"
                    + "\"message\":\"需要存储权限，请在弹窗中允许后重试\"}";
        }
        final JSONObject out = new JSONObject();
        try {
            out.put("ok", true);
            out.put("pending", true);
            out.put("message", "正在生成 HTML…");
        } catch (Exception ignored) {
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String path = HtmlShare.saveToDownloads(act, id);
                if (path == null) {
                    act.pushEvent(toast("保存失败，请确认模型已生成后重试"));
                    return;
                }
                act.pushEvent(toast("已保存到 " + path));
            }
        }, "diandi-savehtml").start();
        return out.toString();
    }

    /** 推一条 toast 事件给页面 */
    private static String toast(String msg) {
        try {
            return new JSONObject().put("type", "toast").put("message", msg).toString();
        } catch (Exception e) {
            return "{\"type\":\"toast\",\"message\":\"\"}";
        }
    }

    @JavascriptInterface
    public String listProjects() {
        return ProjectStore.list(act).toString();
    }

    @JavascriptInterface
    public boolean renameProject(String id, String name) {
        return ProjectStore.rename(act, id, name);
    }

    @JavascriptInterface
    public boolean deleteProject(String id) {
        return ProjectStore.delete(act, id);
    }

    /**
     * 手动清扫失败/中断项目残留的中间产物。
     * 返回 {"scanned":N,"cleaned":M,"freedBytes":B}
     */
    @JavascriptInterface
    public String sweepProjects() {
        return ProjectStore.sweep(act).toString();
    }

    @JavascriptInterface
    public String projectInfo(String id) {
        JSONObject m = ProjectStore.meta(act, id);
        if (m == null) return "{}";
        try {
            m.put("plyUrl", "/model/" + id + ".ply");
            m.put("coverUrl", "/cover/" + id + ".jpg");
        } catch (Exception ignored) {
        }
        return m.toString();
    }

    // ==================== 3DGS 编辑（见 EditStore） ====================

    /**
     * 编辑状态：{ok, revision, sourceSplatCount, deletedCount, crop, transform,
     * hasMask, editPlyExists}
     */
    @JavascriptInterface
    public String editState(String id) {
        return EditStore.state(act, id).toString();
    }

    /**
     * 当前 mask 位图：{ok:true, payloadBase64:..} 或 {ok:false, message:..}
     * 没有编辑过返回 {ok:true, payloadBase64:""}，由 JS 侧自行决定。
     */
    @JavascriptInterface
    public String editMask(String id) {
        JSONObject out = new JSONObject();
        byte[] p = EditStore.readMask(act, id);
        try {
            if (p == null) {
                out.put("ok", true);
                out.put("payloadBase64", "");
            } else {
                out.put("ok", true);
                out.put("payloadBase64", Base64.encodeToString(p, Base64.NO_WRAP));
            }
        } catch (Exception e) {
            Log.w(TAG, "editMask failed: " + e.getMessage());
            try {
                out.put("ok", false);
                out.put("message", "读取 mask 失败：" + e.getMessage());
            } catch (Exception ignored) {
            }
        }
        return out.toString();
    }

    /**
     * 提交一次笔刷删除。json：{id, payloadBase64, revision, sourceSplatCount}
     * 返回 {ok:true, revision, deletedCount} 或 {ok:false, message}
     */
    @JavascriptInterface
    public String saveEditMask(String json) {
        JSONObject out = new JSONObject();
        try {
            JSONObject j = new JSONObject(json);
            String id = j.optString("id", "");
            byte[] payload = Base64.decode(j.optString("payloadBase64", ""), Base64.DEFAULT);
            int rev = j.optInt("revision", 0);
            long src = j.optLong("sourceSplatCount", 0);
            boolean ok = EditStore.writeMask(act, id, payload, rev, src);
            out.put("ok", ok);
            if (ok) {
                out.put("revision", rev);
                out.put("deletedCount", EditStore.popcount(payload));
            } else {
                out.put("message", "保存 mask 失败（位图长度或校验不合法，详见 logcat）");
            }
        } catch (Exception e) {
            Log.w(TAG, "saveEditMask failed: " + e.getMessage());
            try {
                out.put("ok", false);
                out.put("message", "保存 mask 失败：" + e.getMessage());
            } catch (Exception ignored) {
            }
        }
        return out.toString();
    }

    /** 应用 mask + crop 导出破坏性的 edit.ply，返回 {ok, path, splats} */
    @JavascriptInterface
    public String exportEdit(String id) {
        return EditStore.exportEditJson(act, id).toString();
    }

    /** json：{id, position:[x,y,z], rotation:[x,y,z]（欧拉角，度）, scale:1.0} */
    @JavascriptInterface
    public String saveEditTransform(String json) {
        JSONObject out = new JSONObject();
        try {
            JSONObject j = new JSONObject(json);
            String id = j.optString("id", "");
            double[] pos = dbl3(j.optJSONArray("position"));
            double[] rot = dbl3(j.optJSONArray("rotation"));
            double scale = j.optDouble("scale", 1.0);
            boolean ok = EditStore.saveTransform(act, id, pos, rot, scale);
            out.put("ok", ok);
            if (!ok) out.put("message", "position/rotation 必须是 3 元素数组");
        } catch (Exception e) {
            Log.w(TAG, "saveEditTransform failed: " + e.getMessage());
            try {
                out.put("ok", false);
                out.put("message", "保存变换失败：" + e.getMessage());
            } catch (Exception ignored) {
            }
        }
        return out.toString();
    }

    /**
     * json：{id, kind:"sphere"|"box"|null, center:[x,y,z], radius:r}
     * 或 {id, kind:"box", center:[x,y,z], size:[sx,sy,sz]}
     */
    @JavascriptInterface
    public String saveEditCrop(String json) {
        JSONObject out = new JSONObject();
        try {
            JSONObject j = new JSONObject(json);
            String id = j.optString("id", "");
            String kind = j.isNull("kind") ? null : j.optString("kind", "");
            double[] center = dbl3(j.optJSONArray("center"));
            double[] param;
            if ("box".equals(kind)) {
                param = dbl3(j.optJSONArray("size"));
            } else {
                JSONArray r = j.optJSONArray("radius");
                if (r != null) {
                    param = new double[]{r.optDouble(0, 0)};
                } else {
                    param = new double[]{j.optDouble("radius", 0)};
                }
            }
            boolean ok = EditStore.saveCrop(act, id, kind, center, param);
            out.put("ok", ok);
            if (!ok) out.put("message", "保存裁剪失败（kind 必须是 sphere/box/null，参数需 3 元素）");
        } catch (Exception e) {
            Log.w(TAG, "saveEditCrop failed: " + e.getMessage());
            try {
                out.put("ok", false);
                out.put("message", "保存裁剪失败：" + e.getMessage());
            } catch (Exception ignored) {
            }
        }
        return out.toString();
    }

    /** 清空全部编辑（mask / crop / transform / edit.ply） */
    @JavascriptInterface
    public String resetEdits(String id) {
        JSONObject out = new JSONObject();
        try {
            out.put("ok", EditStore.reset(act, id));
        } catch (Exception ignored) {
        }
        return out.toString();
    }

    private static double[] dbl3(JSONArray a) {
        if (a == null || a.length() < 3) return null;
        return new double[]{a.optDouble(0, 0), a.optDouble(1, 0), a.optDouble(2, 0)};
    }

    /** 兜底：让页面能把异常栈送到 logcat */
    @JavascriptInterface
    public void log(String msg) {
        Log.i("DIANDI_JS", msg == null ? "" : msg);
    }
}
