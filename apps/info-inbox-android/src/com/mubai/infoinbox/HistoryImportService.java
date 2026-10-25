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
import android.os.SystemClock;
import org.json.JSONObject;

/** A visible, interruptible full-history run; every request and batch stays bounded. */
public final class HistoryImportService extends Service {
  private static final int NOTICE = 8820;
  private static final String CHANNEL = "history-import";
  private final Object networkSignal = new Object();
  private Thread worker;
  private ConnectivityManager network;
  private final ConnectivityManager.NetworkCallback changes =
      new ConnectivityManager.NetworkCallback() {
        @Override
        public void onAvailable(Network value) {
          wake();
        }

        @Override
        public void onLost(Network value) {
          wake();
        }

        @Override
        public void onCapabilitiesChanged(Network value, android.net.NetworkCapabilities caps) {
          wake();
        }
      };

  private void wake() {
    synchronized (networkSignal) {
      networkSignal.notifyAll();
    }
  }

  public static void start(Context context) {
    if (!new BackendSettings(context).ready() || !AnalysisRouting.ready(context))
      throw new IllegalStateException("请先连接宁宁后端");
    try (MemoryStore memory = new MemoryStore(context)) {
      new OrganizerStore(memory).retryFailed();
    }
    HistoryImport.request(context);
    AnalysisJob.schedule(context, new ApiSettings(context).automatic());
    AnalysisJob.enqueueIntake(context, 1000L);
    KnowledgeJob.enqueue(context);
    context
        .getSharedPreferences("knowledge", MODE_PRIVATE)
        .edit()
        .putBoolean("enabled", true)
        .commit();
    try {
      context.startForegroundService(new Intent(context, HistoryImportService.class));
    } catch (RuntimeException failure) {
      HistoryImport.pause(context, "系统暂未允许开始，请在 App 内重新点击继续");
      throw failure;
    }
  }

  public static void resume(Context context) {
    if (HistoryImport.requested(context))
      context.startForegroundService(new Intent(context, HistoryImportService.class));
  }

  public static void pause(Context context) {
    HistoryImport.pause(context, "全量处理已暂停，原文与已完成进度保留");
    context.stopService(new Intent(context, HistoryImportService.class));
  }

  @Override
  public void onCreate() {
    super.onCreate();
    NotificationManager notices = getSystemService(NotificationManager.class);
    notices.createNotificationChannel(
        new NotificationChannel(CHANNEL, "历史资料处理", NotificationManager.IMPORTANCE_LOW));
    network = getSystemService(ConnectivityManager.class);
    network.registerDefaultNetworkCallback(changes);
  }

  private Notification notification() {
    PendingIntent open =
        PendingIntent.getActivity(
            this,
            NOTICE,
            new Intent(this, MainActivity.class).putExtra("tab", 1),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    PendingIntent pause =
        PendingIntent.getService(
            this,
            NOTICE,
            new Intent(this, HistoryImportService.class).setAction("pause"),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    return new Notification.Builder(this, CHANNEL)
        .setSmallIcon(android.R.drawable.stat_notify_sync)
        .setContentTitle("正在全量整理历史资料")
        .setContentText(HistoryImport.status(this))
        .setStyle(new Notification.BigTextStyle().bigText(HistoryImport.status(this)))
        .setContentIntent(open)
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .addAction(new Notification.Action.Builder(null, "暂停", pause).build())
        .build();
  }

  @Override
  public int onStartCommand(Intent intent, int flags, int id) {
    if (intent != null && "pause".equals(intent.getAction())) {
      pause(this);
      return START_NOT_STICKY;
    }
    if (!HistoryImport.requested(this)) {
      stopSelf();
      return START_NOT_STICKY;
    }
    if (Build.VERSION.SDK_INT >= 29)
      startForeground(NOTICE, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
    else startForeground(NOTICE, notification());
    if (worker == null || !worker.isAlive()) {
      worker = new Thread(this::run, "history-import");
      worker.start();
    }
    return START_STICKY;
  }

  private void run() {
    int errors = 0;
    boolean collected = false;
    long deadline = SystemClock.elapsedRealtime() + 5L * 60 * 60 * 1000;
    try {
      while (HistoryImport.requested(this) && !Thread.currentThread().isInterrupted()) {
        if (SystemClock.elapsedRealtime() >= deadline) {
          HistoryImport.defer(this, "持续处理已转到系统后台任务，进度保留；打开 App 可加速继续");
          break;
        }
        if (!KnowledgeSync.wifi(this)) {
          HistoryImport.waiting(this);
          publish();
          synchronized (networkSignal) {
            networkSignal.wait(30000);
          }
          continue;
        }
        try {
          if (!collected) {
            phase("collection", "正在核对并补采微信历史，原有进度保留");
            new RootGateway(this).prepareHistoryCollection();
            collected = true;
          }
          phase("intake", "正在将已采集历史加入整理队列");
          int intake = AnalysisEngine.intake(this, true);
          phase("knowledge", "正在同步知识库，本机原文与进度保留");
          KnowledgeSync.run(this, true);
          if (!HistoryImport.requested(this)) break;
          phase("analysis", "正在生成 AI 建议，完成后更新进度");
          AnalysisEngine.run(this, true);
          if (!HistoryImport.requested(this)) break;
          try (MemoryStore memory = new MemoryStore(this)) {
            OrganizerStore organizer = new OrganizerStore(memory);
            JSONObject stats = organizer.queueStats();
            KnowledgeStore knowledge = new KnowledgeStore(memory, new BackendSettings(this).base());
            int remaining =
                new RootGateway(this).knowledgeCandidates(memory, knowledge.space, true).length();
            HistoryImport.progress(this, stats, new JSONObject(knowledge.meta("status", "{}")));
            if (stats.optInt("failed") > 0) {
              HistoryImport.pause(this, "有整理批次未完成，点击继续会重试失败批次");
              break;
            }
            if (knowledge.conflictCount() > 0) {
              HistoryImport.pause(this, "存在双端修改冲突，请在知识地图核对后继续");
              break;
            }
            if (HistoryImport.drained(intake, stats, remaining, knowledge.mutations().length())) {
              HistoryImport.complete(this);
              break;
            }
          }
          publish();
          errors = 0;
        } catch (Exception failure) {
          if (Thread.currentThread().isInterrupted() || failure instanceof InterruptedException)
            throw failure;
          if (++errors >= 5) {
            HistoryImport.pause(this, "连接或 Root 读取暂未完成，进度已保存，点击继续重试");
            break;
          }
          getSharedPreferences("history-import", MODE_PRIVATE)
              .edit()
              .putString(
                  "last_error_code",
                  failure instanceof RootGateway.RootFailure
                      ? ((RootGateway.RootFailure) failure).code
                      : failure.getClass().getSimpleName())
              .putString("message", "连接暂未完成，已保存进度，稍后自动重试")
              .commit();
          publish();
          synchronized (networkSignal) {
            networkSignal.wait(60000L * errors);
          }
        }
      }
    } catch (Exception interrupted) {
      // Pause intent or process shutdown never promotes an incomplete run to completed.
    } finally {
      Thread finished = Thread.currentThread();
      new android.os.Handler(getMainLooper())
          .post(
              () -> {
                if (worker != finished) return;
                worker = null;
                stopForeground(STOP_FOREGROUND_REMOVE);
                stopSelf();
              });
    }
  }

  private void publish() {
    getSystemService(NotificationManager.class).notify(NOTICE, notification());
  }

  private void phase(String phase, String message) {
    if (!HistoryImport.requested(this)) return;
    android.content.SharedPreferences.Editor edit =
        getSharedPreferences("history-import", MODE_PRIVATE).edit().putString("phase", phase);
    if (phase.equals("collection")) edit.putString("message", message);
    edit.apply();
    publish();
  }

  @Override
  public void onTimeout(int startId, int type) {
    HistoryImport.defer(this, "系统暂缓了长时间传输，进度已保留，后台会继续补齐");
    stopSelf();
  }

  @Override
  public void onDestroy() {
    if (worker != null) worker.interrupt();
    if (network != null) network.unregisterNetworkCallback(changes);
    super.onDestroy();
  }

  @Override
  public IBinder onBind(Intent intent) {
    return null;
  }
}
