package com.diandi.gaosi;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 进程内共享状态：前台服务写进度，Activity 读取并推给 WebView。
 * 全部字段用 volatile / synchronized，因为读写发生在不同线程。
 */
public final class AppState {

    public interface ProgressSink {
        void push(String json);
    }

    private static volatile String progressJson = "{\"active\":false}";
    private static volatile ProgressSink sink;

    private AppState() {
    }

    public static void setSink(ProgressSink s) {
        sink = s;
    }

    public static String getProgress() {
        return progressJson;
    }

    public static void setProgress(String json) {
        progressJson = json;
        ProgressSink s = sink;
        if (s != null) {
            try {
                s.push(json);
            } catch (Throwable ignored) {
            }
        }
    }

    public static JSONObject newBase(String jobId, String projectId, String quality) {
        JSONObject o = new JSONObject();
        try {
            o.put("active", true);
            o.put("jobId", jobId);
            o.put("projectId", projectId);
            o.put("quality", quality);
            o.put("state", "running");
            o.put("log", new JSONArray());
            o.put("stages", new JSONArray());
        } catch (Exception ignored) {
        }
        return o;
    }
}
