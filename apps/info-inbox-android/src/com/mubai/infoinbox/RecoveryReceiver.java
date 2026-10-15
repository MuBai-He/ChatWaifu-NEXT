package com.mubai.infoinbox;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class RecoveryReceiver extends BroadcastReceiver {
  @Override
  public void onReceive(Context context, Intent intent) {
    String action = intent.getAction();
    if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
        && !Intent.ACTION_TIME_CHANGED.equals(action)
        && !Intent.ACTION_TIMEZONE_CHANGED.equals(action)
        && !"android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED".equals(action))
      return;
    PendingResult result = goAsync();
    new Thread(
            () -> {
              try {
                ReminderScheduler.reconcile(context);
                KnowledgeJob.enqueue(context);
                AnalysisJob.schedule(context, new ApiSettings(context).automatic());
                AnalysisJob.enqueueIntake(context, 30000L);
              } catch (Exception ignored) {
                /* Persistent data is retried on the next foreground resume. */
              } finally {
                result.finish();
              }
            },
            "inbox-recovery")
        .start();
  }
}
