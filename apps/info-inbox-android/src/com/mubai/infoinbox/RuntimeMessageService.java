package com.mubai.infoinbox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import java.util.concurrent.atomic.AtomicBoolean;

/** Visible connection for Ningning's messages across devices; performs no AI. */
public final class RuntimeMessageService extends Service {
  private final Object wake = new Object();
  private final AtomicBoolean syncRequested = new AtomicBoolean(true);
  private Thread worker;
  private ConnectivityManager network;
  private final ConnectivityManager.NetworkCallback changes = new ConnectivityManager.NetworkCallback() {
    @Override public void onAvailable(Network value) { syncRequested.set(true); signal(); }
    @Override public void onLost(Network value) { signal(); }
  };
  private void signal() {
    synchronized (wake) { wake.notifyAll(); }
    RuntimeProcessing.wakeActivityPoll();
  }

  static boolean enabled(Context context) {
    return KnowledgeSync.enabled(context) && context.getSharedPreferences("runtime-processing", MODE_PRIVATE).getBoolean("message_connection", true);
  }
  static void ensureStarted(Context context) {
    if (!enabled(context) || !RuntimeProcessing.owns(context) || !new BackendSettings(context).ready()) return;
    try { context.startForegroundService(new Intent(context, RuntimeMessageService.class)); }
    catch (RuntimeException unavailable) { KnowledgeJob.enqueue(context); }
  }
  static boolean requestSync(Context context) {
    if (!enabled(context) || !RuntimeProcessing.owns(context)
        || !new BackendSettings(context).ready()) return false;
    KnowledgeJob.schedule(context);
    try {
      return context.startForegroundService(new Intent(context, RuntimeMessageService.class)
          .setAction("sync")) != null;
    } catch (RuntimeException unavailable) {
      return false; // The caller retains the durable JobScheduler fallback.
    }
  }
  private boolean syncCycle() throws Exception {
    PowerManager.WakeLock transfer = getSystemService(PowerManager.class).newWakeLock(
        PowerManager.PARTIAL_WAKE_LOCK, getPackageName() + ":source-sync");
    transfer.acquire(120000L);
    try {
      return KnowledgeSync.runCycle(this,
          HistoryImport.requested(this) && KnowledgeSync.wifi(this));
    } finally {
      if (transfer.isHeld()) transfer.release();
    }
  }
  @Override public void onCreate() {
    super.onCreate();
    NotificationManager notices = getSystemService(NotificationManager.class);
    NotificationChannel channel = new NotificationChannel("ningning-connection", "宁宁消息连接", NotificationManager.IMPORTANCE_LOW);
    channel.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
    notices.createNotificationChannel(channel);
    PendingIntent open = PendingIntent.getActivity(this, 8825, new Intent(this, HarnessActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    PendingIntent stop = PendingIntent.getService(this, 8826, new Intent(this, RuntimeMessageService.class).setAction("stop"), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    Notification notification = new Notification.Builder(this, "ningning-connection")
        .setSmallIcon(R.drawable.app_icon).setContentTitle("与宁宁保持消息连接")
        .setContentText("接收重要变化；普通记忆保存保持安静")
        .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
        .addAction(new Notification.Action.Builder(null, "停止及时连接", stop).build()).build();
    if (Build.VERSION.SDK_INT >= 34) startForeground(8825, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING);
    else startForeground(8825, notification);
    network = getSystemService(ConnectivityManager.class);
    network.registerDefaultNetworkCallback(changes);
  }
  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (intent != null && "stop".equals(intent.getAction())) {
      getSharedPreferences("runtime-processing", MODE_PRIVATE).edit().putBoolean("message_connection", false).apply();
      stopSelf(); return START_NOT_STICKY;
    }
    if (intent != null && "sync".equals(intent.getAction())) {
      syncRequested.set(true);
      signal();
    }
    if (worker != null) return START_STICKY;
    worker = new Thread(() -> {
      while (!Thread.currentThread().isInterrupted() && enabled(this) && RuntimeProcessing.owns(this)) {
        try {
          boolean more = false;
          if (syncRequested.getAndSet(false)) {
            more = syncCycle();
            getSharedPreferences("knowledge", MODE_PRIVATE).edit()
                .remove("connection_error_type").remove("connection_error_origin").apply();
            if (more) syncRequested.set(true);
            HistoryImport.maybeComplete(this);
          }
          // Drain known backlog without a 25-second idle poll between bounded pages.
          // With no work, the existing long poll remains the only network wait.
          if (RuntimeProcessing.receiveActivity(this, !more)) syncRequested.set(true);
        } catch (InterruptedException stopped) {
          Thread.currentThread().interrupt(); break;
        } catch (Exception unavailable) {
          String origin = "unknown";
          for (StackTraceElement frame : unavailable.getStackTrace()) {
            if (frame.getClassName().startsWith("com.mubai.infoinbox.")) {
              origin = frame.getMethodName() + ":" + frame.getLineNumber();
              break;
            }
          }
          getSharedPreferences("knowledge", MODE_PRIVATE).edit()
              .putString("connection_error_type", unavailable.getClass().getSimpleName())
              .putString("connection_error_origin", origin).apply();
          syncRequested.set(true);
          synchronized (wake) {
            try { wake.wait(30000); }
            catch (InterruptedException stopped) { Thread.currentThread().interrupt(); break; }
          }
        }
      }
      stopSelf();
    }, "ningning-messages");
    worker.start();
    return START_STICKY;
  }
  @Override public void onDestroy() {
    if (worker != null) worker.interrupt();
    if (network != null) network.unregisterNetworkCallback(changes);
    signal(); stopForeground(STOP_FOREGROUND_REMOVE); super.onDestroy();
  }
  @Override public IBinder onBind(Intent intent) { return null; }
}
