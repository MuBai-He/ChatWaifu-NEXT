package com.mubai.infoinbox;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import java.util.concurrent.ConcurrentHashMap;

public final class KnowledgeJob extends JobService {
  private final ConcurrentHashMap<Integer, Thread> tasks = new ConcurrentHashMap<>();

  public static void schedule(Context context) {
    JobScheduler jobs = context.getSystemService(JobScheduler.class);
    if (!KnowledgeSync.enabled(context)) {
      jobs.cancel(8810);
      jobs.cancel(8811);
      return;
    }
    if (jobs.getPendingJob(8810) == null)
      jobs.schedule(
          new JobInfo.Builder(8810, new ComponentName(context, KnowledgeJob.class))
              .setPeriodic(15 * 60 * 1000L)
              .setPersisted(true)
              .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
              .build());
  }

  public static void enqueue(Context context) {
    schedule(context);
    if (!KnowledgeSync.enabled(context)) return;
    JobScheduler jobs = context.getSystemService(JobScheduler.class);
    if (jobs.getPendingJob(8811) == null)
      jobs.schedule(
          new JobInfo.Builder(8811, new ComponentName(context, KnowledgeJob.class))
              .setMinimumLatency(30000L)
              .setPersisted(true)
              .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
              .build());
  }

  public static void enqueueNow(Context context) {
    schedule(context);
    if (!KnowledgeSync.enabled(context)) return;
    context
        .getSystemService(JobScheduler.class)
        .schedule(
            new JobInfo.Builder(8811, new ComponentName(context, KnowledgeJob.class))
                .setMinimumLatency(0L)
                .setPersisted(true)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .build());
  }

  @Override
  public boolean onStartJob(JobParameters params) {
    android.util.Log.i("InboxKnowledge", "job_started id=" + params.getJobId());
    Thread task =
        new Thread(
            () -> {
              boolean retry = false;
              try {
                KnowledgeSync.run(this, HistoryImport.requested(this) && KnowledgeSync.wifi(this));
                HistoryImport.maybeComplete(this);
              } catch (Exception error) {
                if (error instanceof InterruptedException) Thread.currentThread().interrupt();
                retry = true;
                android.util.Log.w("InboxKnowledge", "job_failed id=" + params.getJobId()
                    + " type=" + error.getClass().getSimpleName());
                getSharedPreferences("knowledge", MODE_PRIVATE)
                    .edit()
                    .putString("error", "同步暂未完成，本地修改和进度已保留")
                    .apply();
              } finally {
                android.util.Log.i("InboxKnowledge", "job_finished id=" + params.getJobId()
                    + " retry=" + retry);
                if (tasks.remove(params.getJobId(), Thread.currentThread()))
                  jobFinished(params, retry);
                if (!Thread.currentThread().isInterrupted()
                    && HistoryImport.requested(this)
                    && KnowledgeSync.wifi(this)) enqueue(this);
              }
            },
            "knowledge-sync");
    tasks.put(params.getJobId(), task);
    task.start();
    return true;
  }

  @Override
  public boolean onStopJob(JobParameters params) {
    android.util.Log.i("InboxKnowledge", "job_stopped id=" + params.getJobId()
        + " reason=" + (android.os.Build.VERSION.SDK_INT >= 31 ? params.getStopReason() : -1));
    Thread task = tasks.remove(params.getJobId());
    if (task != null) task.interrupt();
    return true;
  }
}
