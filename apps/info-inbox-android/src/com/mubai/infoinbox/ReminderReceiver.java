package com.mubai.infoinbox;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class ReminderReceiver extends BroadcastReceiver {
  @Override
  public void onReceive(Context context, Intent intent) {
    PendingResult result = goAsync();
    new Thread(
            () -> {
              try {
                String id = intent.getStringExtra("work_id");
                if (id == null) return;
                if ("com.mubai.infoinbox.REMIND".equals(intent.getAction())) {
                  ReminderScheduler.deliver(
                      context,
                      id,
                      intent.getIntExtra("revision", -1),
                      intent.getLongExtra("due_at", -1));
                } else if ("com.mubai.infoinbox.ACTION".equals(intent.getAction())) {
                  ReminderScheduler.acknowledge(
                      context,
                      id,
                      intent.getIntExtra("revision", -1),
                      intent.getLongExtra("due_at", -1),
                      intent.getStringExtra("choice"));
                }
              } catch (Exception ignored) {
                /* Durable rule remains inspectable; never log private titles. */
              } finally {
                result.finish();
              }
            },
            "inbox-reminder")
        .start();
  }
}
