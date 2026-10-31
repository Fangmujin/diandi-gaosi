package com.diandi.gaosi;

import android.content.Context;
import android.util.Log;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 本机 HTTP 服务（127.0.0.1 随机端口）：WebView 需要 http(s) 源才能用 ESM/importmap/fetch。
 * 路由：
 *   /                       -> assets/index.html
 *   /<path>                 -> assets/<path>
 *   /thumb?p=<rel>          -> filesDir/imports/<rel> 缩略图（jpeg）
 *   /cover/<id>.jpg         -> filesDir/projects/<id>/cover.jpg
 *   /model/<id>.ply         -> filesDir/projects/<id>/final.ply
 */
public class HttpServer {

    private static final String TAG = "DiandiHttp";
    private static final ExecutorService POOL = Executors.newFixedThreadPool(8);
    private final ServerSocket serverSocket;
    private final Context ctx;
    private volatile boolean running = true;

    public HttpServer(Context c) throws IOException {
        this.ctx = c.getApplicationContext();
        this.serverSocket = new ServerSocket(0);
    }

    public int getPort() {
        return serverSocket.getLocalPort();
    }

    public void start() {
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                loop();
            }
        }, "diandi-http");
        t.setDaemon(true);
        t.start();
    }

    public void stop() {
        running = false;
        try {
            serverSocket.close();
        } catch (IOException ignored) {
        }
    }

    private void loop() {
        while (running) {
            try {
                final Socket s = serverSocket.accept();
                // 必须一连接一线程：WebView 会预开 2-3 条空闲 socket，单线程串行处理时
                // 每条都要等 SO_TIMEOUT 才释放，实测会把 9MB 资产的冷启动拖到 1-2 分钟、
                // 28MB 模型的 fetch 拖到 117 秒。
                POOL.execute(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            handle(s);
                        } finally {
                            try {
                                s.close();
                            } catch (IOException ignored) {
                            }
                        }
                    }
                });
            } catch (IOException e) {
                if (running) Log.w(TAG, "accept: " + e.getMessage());
            }
        }
    }

    private void handle(Socket s) {
        try {
            s.setSoTimeout(10000);
            s.setTcpNoDelay(true);
            InputStream in = new BufferedInputStream(s.getInputStream(), 8192);
            String requestLine = readLine(in);
            if (requestLine == null || requestLine.isEmpty()) {
                s.close();
                return;
            }
            String[] parts = requestLine.split(" ");
            String method = parts.length > 0 ? parts[0] : "";
            String rawPath = parts.length > 1 ? parts[1] : "/";
            String line;
            while ((line = readLine(in)) != null && !line.isEmpty()) {
                // drain headers
            }
            if (!"GET".equalsIgnoreCase(method)) {
                respond(s.getOutputStream(), 405, "text/plain", "method not allowed".getBytes(StandardCharsets.UTF_8));
                s.close();
                return;
            }

            int q = rawPath.indexOf('?');
            String path = q >= 0 ? rawPath.substring(0, q) : rawPath;
            Map<String, String> query = parseQuery(q >= 0 ? rawPath.substring(q + 1) : "");
            try {
                path = URLDecoder.decode(path, StandardCharsets.UTF_8.name());
            } catch (Exception ignored) {
            }

            byte[] data = null;
            String mime = "application/octet-stream";

            if (path.equals("/thumb")) {
                String rel = query.get("p");
                File f = resolve(new File(ctx.getFilesDir(), "imports"), rel);
                data = MediaImport.thumb(ctx, f, 256);
                mime = "image/jpeg";
            } else if (path.startsWith("/cover/")) {
                String id = path.substring("/cover/".length()).replace(".jpg", "");
                File f = new File(ProjectStore.dir(ctx, id), "cover.jpg");
                if (f.isFile()) data = readFile(f);
                mime = "image/jpeg";
            } else if (path.startsWith("/model/")) {
                String id = path.substring("/model/".length()).replace(".ply", "");
                File f = new File(ProjectStore.dir(ctx, id), "final.ply");
                if (f.isFile()) data = readFile(f);
                mime = "application/octet-stream";
            } else {
                String rel = path.equals("/") ? "index.html" : path.substring(1);
                mime = mimeFor(rel);
                try {
                    InputStream ais = ctx.getAssets().open(rel);
                    data = readAll(ais);
                } catch (IOException ignored) {
                }
            }

            OutputStream out = s.getOutputStream();
            if (data == null) {
                respond(out, 404, "text/plain", ("NOT FOUND " + path).getBytes(StandardCharsets.UTF_8));
            } else {
                respond(out, 200, mime, data);
            }
            out.flush();
            s.close();
        } catch (Exception e) {
            Log.w(TAG, "handle: " + e.getMessage());
            try {
                s.close();
            } catch (IOException ignored) {
            }
        }
    }

    /** 防目录穿越：解析后必须仍在 root 下 */
    private static File resolve(File root, String rel) {
        if (rel == null) return null;
        try {
            File f = new File(root, rel).getCanonicalFile();
            String rp = root.getCanonicalPath();
            if (f.getPath().startsWith(rp)) return f;
        } catch (IOException ignored) {
        }
        return null;
    }

    private static Map<String, String> parseQuery(String q) {
        Map<String, String> m = new HashMap<>();
        if (q.isEmpty()) return m;
        for (String kv : q.split("&")) {
            int i = kv.indexOf('=');
            if (i > 0) {
                try {
                    m.put(URLDecoder.decode(kv.substring(0, i), "UTF-8"),
                            URLDecoder.decode(kv.substring(i + 1), "UTF-8"));
                } catch (Exception ignored) {
                }
            }
        }
        return m;
    }

    private static byte[] readFile(File f) {
        try {
            FileInputStream is = new FileInputStream(f);
            byte[] b = readAll(is);
            is.close();
            return b;
        } catch (IOException e) {
            return null;
        }
    }

    private static String mimeFor(String rel) {
        String l = rel.toLowerCase(Locale.ROOT);
        if (l.endsWith(".html") || l.endsWith(".htm")) return "text/html; charset=utf-8";
        if (l.endsWith(".js") || l.endsWith(".mjs")) return "text/javascript; charset=utf-8";
        if (l.endsWith(".css")) return "text/css; charset=utf-8";
        if (l.endsWith(".json")) return "application/json";
        if (l.endsWith(".wasm")) return "application/wasm";
        if (l.endsWith(".svg")) return "image/svg+xml";
        if (l.endsWith(".png")) return "image/png";
        if (l.endsWith(".jpg") || l.endsWith(".jpeg")) return "image/jpeg";
        if (l.endsWith(".webp")) return "image/webp";
        if (l.endsWith(".woff2")) return "font/woff2";
        if (l.endsWith(".ply")) return "application/octet-stream";
        if (l.endsWith(".glb")) return "model/gltf-binary";
        if (l.endsWith(".txt") || l.endsWith(".md")) return "text/plain; charset=utf-8";
        return "application/octet-stream";
    }

    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        int prev = -1, c;
        while ((c = in.read()) != -1) {
            if (prev == '\r' && c == '\n') break;
            if (c != '\r') bos.write(c);
            prev = c;
        }
        if (bos.size() == 0 && c == -1) return null;
        return new String(bos.toByteArray(), StandardCharsets.UTF_8).trim();
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int r;
        while ((r = in.read(buf)) != -1) bos.write(buf, 0, r);
        return bos.toByteArray();
    }

    private static void respond(OutputStream raw, int code, String mime, byte[] body) throws IOException {
        String reason = code == 200 ? "OK" : (code == 404 ? "Not Found" : "Error");
        String head = "HTTP/1.1 " + code + " " + reason + "\r\n"
                + "Content-Type: " + mime + "\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Cache-Control: no-store\r\n"
                + "Access-Control-Allow-Origin: *\r\n"
                + "Connection: close\r\n\r\n";
        // 大负载（final.ply 动辄 20-50MB）必须经 BufferedOutputStream 分块写出：
        // 直接 out.write(byte[28M]) 在模拟器回环上实测只有 ~250KB/s，加载一个模型要两分钟。
        BufferedOutputStream out = new BufferedOutputStream(raw, 65536);
        out.write(head.getBytes(StandardCharsets.UTF_8));
        int off = 0;
        while (off < body.length) {
            int n = Math.min(65536, body.length - off);
            out.write(body, off, n);
            off += n;
        }
        out.flush();
    }
}
