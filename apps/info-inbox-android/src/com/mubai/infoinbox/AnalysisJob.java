package com.mubai.infoinbox;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import java.util.concurrent.ConcurrentHashMap;

/** Collection intake stays local; only explicitly enabled analysis uses the configured API. */
public final class AnalysisJob extends JobService {
  private final ConcurrentHashMap<Integer, Thread> tasks = new ConcurrentHashMap<>();

  public static void schedule(Context context, boolean enabled) {
    JobScheduler jobs = context.getSystemService(JobScheduler.class);
    jobs.schedule(
        new JobInfo.Builder(8801, new ComponentName(context, AnalysisJob.class))
            .setPeriodic(15 * 60 * 1000L)
            .setPersisted(true)
            .build());
    if (!enabled && !HistoryImport.requested(context)) jobs.cancel(8802);
  }

  public static void enqueueIntake(Context context, long delay) {
    enqueueJob(context, 8803, delay, false);
  }

  public static void enqueue(Context context, long delay) {
    ApiSettings settings = new ApiSettings(context);
    if ((!settings.automatic() && !HistoryImport.requested(context))
        || !AnalysisRouting.ready(context)) return;
    enqueueJob(context, 8802, delay, true);
  }

  private static void enqueueJob(Context context, int id, long delay, boolean network) {
    JobScheduler jobs = context.getSystemService(JobScheduler.class);
    if (jobs.getPendingJob(id) != null) return;
    JobInfo.Builder job =
        new JobInfo.Builder(id, new ComponentName(context, AnalysisJob.class))
            .setMinimumLatency(delay)
            .setPersisted(true);
    if (network) job.setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY);
    jobs.schedule(job.build());
  }

  @Override
  public boolean onStartJob(JobParameters params) {
    final int id = params.getJobId();
    Thread task =
        new Thread(
            () -> {
              boolean more = false;
              try {
                if (id == 8802) {
                  boolean full = HistoryImport.requested(this) && KnowledgeSync.wifi(this);
                  if (!new ApiSettings(this).automatic() && !full) return;
                  AnalysisEngine.run(this, full);
                  try (MemoryStore memory = new MemoryStore(this)) {
                    OrganizerStore organizer = new OrganizerStore(memory);
                    more = organizer.pendingWithinScope(full ? 0 : organizer.intakeFloor()) > 0;
                  }
                } else {
                  long dirty =
                      getSharedPreferences("collector-events", MODE_PRIVATE).getLong("dirty_at", 0);
                  more =
                      AnalysisEngine.intake(
                              this, HistoryImport.requested(this) && KnowledgeSync.wifi(this))
                          >= 20;
                  more |=
                      getSharedPreferences("collector-events", MODE_PRIVATE).getLong("dirty_at", 0)
                          > dirty;
                }
                HistoryImport.maybeComplete(this);
              } catch (Exception ignored) {
                /* Input and redacted failure status remain durable. */
              } finally {
                tasks.remove(id, Thread.currentThread());
                jobFinished(params, false);
                if (!Thread.currentThread().isInterrupted()) {
                  if (id != 8802) {
                    enqueue(this, 1000L);
                    if (more) enqueueIntake(this, 60000L);
                  } else if (more) enqueue(this, 60000L);
                }
              }
            },
            "inbox-job-" + id);
    tasks.put(id, task);
    task.start();
    return true;
  }

  @Override
  public boolean onStopJob(JobParameters params) {
    Thread task = tasks.remove(params.getJobId());
    if (task != null) task.interrupt();
    return true;
  }
}
