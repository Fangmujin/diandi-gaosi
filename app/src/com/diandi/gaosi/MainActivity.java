package com.diandi.gaosi;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.FrameLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/** 应用壳：本地 HTTP 服务 + WebView + JS 桥。UI 全部在 assets/index.html。 */
public class MainActivity extends Activity {

    private static final String TAG = "DiandiMain";
    static final int REQ_PICK = 1001;
    static final int REQ_CAPTURE = 1002;

    private WebView webView;
    private HttpServer http;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final JSONArray pending = new JSONArray();
    private int port = 0;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF0D1117);
        TextView boot = new TextView(this);
        boot.setText("正在启动…");
        boot.setTextColor(0xFF8B949E);
        root.addView(boot, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                android.view.Gravity.CENTER));
        setContentView(root);

        try {
            http = new HttpServer(this);
            http.start();
            port = http.getPort();
        } catch (Exception e) {
            Log.e(TAG, "http server failed", e);
            boot.setText("本地服务启动失败：" + e.getMessage());
            return;
        }

        // 启动即清扫：失败/中断项目残留的 frames/dataset/tmp 是最占空间的部分，
        // 必须在 WebView 拉列表之前回收，否则图库页一上来就是一堆死卡片占着空间。
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    JSONObject r = ProjectStore.sweep(MainActivity.this);
                    Log.i(TAG, "sweep scanned=" + r.optInt("scanned")
                            + " cleaned=" + r.optInt("cleaned")
                            + " freedBytes=" + r.optLong("freedBytes"));
                } catch (Throwable t) {
                    Log.w(TAG, "sweep failed: " + t.getMessage());
                }
            }
        }, "diandi-sweep").start();

        webView = new WebView(this);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        WebView.setWebContentsDebuggingEnabled(true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                Log.i("DIANDI_CONSOLE", m.message() + " @" + m.sourceId() + ":" + m.lineNumber());
                return true;
            }
        });
        webView.addJavascriptInterface(new Bridge(this), "Diandi");

        root.removeAllViews();
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        webView.loadUrl("http://127.0.0.1:" + port + "/index.html");

        AppState.setSink(new AppState.ProgressSink() {
            @Override
            public void push(final String json) {
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        pushEvent("{\"type\":\"progress\",\"data\":" + json + "}");
                    }
                });
            }
        });

        Log.i(TAG, "ready on port " + port);
    }

    int httpPort() {
        return port;
    }

    void pushEvent(String json) {
        if (webView == null) return;
        final String js = "try{window.__native && window.__native(" + json + ")}catch(e){}";
        if (Looper.myLooper() == Looper.getMainLooper()) {
            webView.evaluateJavascript(js, null);
        } else {
            ui.post(new Runnable() {
                @Override
                public void run() {
                    if (webView != null) webView.evaluateJavascript(js, null);
                }
            });
        }
    }

    JSONArray takePending() {
        return pending;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        if (uri == null) return;
        if (requestCode != REQ_PICK && requestCode != REQ_CAPTURE) return;
        try {
            grantUriPermission(getPackageName(), uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (Exception ignored) {
        }
        final JSONObject item = MediaImport.importUri(this, uri);
        if (item == null) {
            pushEvent("{\"type\":\"toast\",\"message\":\"素材读取失败，请换一个文件\"}");
            return;
        }
        pending.put(item);
        pushEvent("{\"type\":\"media\",\"data\":" + item.toString() + "}");
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (http != null) http.stop();
        AppState.setSink(null);
        super.onDestroy();
    }
}
