package com.mubai.infoinbox;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Signature-protected signal contains no message text, identifiers or key. */
public final class CollectorReceiver extends BroadcastReceiver {
  @Override
  public void onReceive(Context context, Intent intent) {
    if ("com.mubai.infoinbox.COLLECTED".equals(intent.getAction())) {
      boolean durable =
          context
              .getSharedPreferences("collector-events", Context.MODE_PRIVATE)
              .edit()
              .putLong("dirty_at", System.currentTimeMillis())
              .commit();
      KnowledgeJob.enqueue(context);
      AnalysisJob.enqueueIntake(context, durable ? 30000L : 0L);
    }
  }
}
