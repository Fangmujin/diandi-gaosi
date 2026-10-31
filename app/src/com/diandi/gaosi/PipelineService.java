package com.diandi.gaosi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

/**
 * 训练/重建前台服务（foregroundServiceType="mediaProcessing"）。
 * 服务只负责生命周期与通知，真正的编排在 PipelineManager。
 */
public class PipelineService extends Service {

    private static final String TAG = "PipelineService";
    private static final String CH = "pipeline";
    private static final int NOTIF_ID = 20261014;

    public static final String ACTION_START = "com.diandi.gaosi.START";
    public static final String ACTION_CANCEL = "com.diandi.gaosi.CANCEL";
    public static final String EXTRA_PROJECT_ID = "projectId";
    public static final String EXTRA_PROJECT_NAME = "projectName";
    public static final String EXTRA_QUALITY = "quality";
    public static final String EXTRA_SOURCE = "source";

    private static volatile PipelineManager current;
    private Thread worker;

    public static void cancel(Context c) {
        PipelineManager m = current;
        if (m != null) m.requestCancel();
        Intent i = new Intent(c, PipelineService.class);
        i.setAction(ACTION_CANCEL);
        try {
            c.startService(i);
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CH, "重建进度",
                    NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("高斯重建任务进行中");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    private Notification buildNotification(String text) {
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            b = new Notification.Builder(this, CH);
        } else {
            b = new Notification.Builder(this);
        }
        b.setContentTitle("点点高斯 · 重建进行中")
                .setContentText(text)
                .setSmallIcon(R.drawable.ic_stat)
                .setOngoing(true)
                .setProgress(100, 0, true);
        return b.build();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_CANCEL.equals(action)) {
            PipelineManager m = current;
            if (m != null) m.requestCancel();
            return START_NOT_STICKY;
        }
        if (!ACTION_START.equals(action)) return START_NOT_STICKY;

        final String projectId = intent.getStringExtra(EXTRA_PROJECT_ID);
        final String projectName = intent.getStringExtra(EXTRA_PROJECT_NAME);
        final String quality = intent.getStringExtra(EXTRA_QUALITY);
        final Uri source = Uri.parse(intent.getStringExtra(EXTRA_SOURCE));

        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIF_ID, buildNotification("准备中…"),
                        android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING);
            } else {
                startForeground(NOTIF_ID, buildNotification("准备中…"));
            }
        } catch (Exception e) {
            Log.w(TAG, "startForeground failed: " + e.getMessage());
        }

        final PipelineManager mgr = new PipelineManager(this, projectId, projectName, quality, source);
        current = mgr;
        worker = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    mgr.run();
                } catch (Throwable t) {
                    Log.e(TAG, "job crashed", t);
                } finally {
                    current = null;
                    try {
                        stopForeground(true);
                    } catch (Exception ignored) {
                    }
                    stopSelf();
                }
            }
        }, "pipeline-worker");
        worker.start();
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        PipelineManager m = current;
        if (m != null) m.requestCancel();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
