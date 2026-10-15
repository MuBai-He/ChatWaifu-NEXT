package com.mubai.infoinbox;

import android.Manifest;
import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import org.json.JSONArray;
import org.json.JSONObject;

/** One process-wide action boundary fences cancellation against notification posting. */
public final class ReminderScheduler {
  static final Object LOCK = new Object();
  private static final String CHANNEL = "confirmed-reminders";

  private ReminderScheduler() {}

  public static boolean notificationsAllowed(Context context) {
    return (Build.VERSION.SDK_INT < 33
            || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED)
        && context.getSystemService(NotificationManager.class).areNotificationsEnabled();
  }

  public static boolean preciseAllowed(Context context) {
    return Build.VERSION.SDK_INT < 31
        || context.getSystemService(AlarmManager.class).canScheduleExactAlarms();
  }

  private static PendingIntent alarm(Context context, JSONObject rule) throws Exception {
    Intent intent =
        new Intent(context, ReminderReceiver.class)
            .setAction("com.mubai.infoinbox.REMIND")
            .setData(Uri.parse("infoinbox://reminder/" + rule.getString("work_id")))
            .putExtra("work_id", rule.getString("work_id"))
            .putExtra("revision", rule.getInt("revision"))
            .putExtra("due_at", rule.getLong("due_at"));
    return PendingIntent.getBroadcast(
        context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
  }

  public static void reconcile(Context context) throws Exception {
    synchronized (LOCK) {
      try (MemoryStore memory = new MemoryStore(context)) {
        OrganizerStore store = new OrganizerStore(memory);
        if (notificationsAllowed(context)) store.retryBlocked(System.currentTimeMillis());
        JSONArray rules = store.reminders();
        AlarmManager manager = context.getSystemService(AlarmManager.class);
        NotificationManager notifications = context.getSystemService(NotificationManager.class);
        long now = System.currentTimeMillis();
        for (int i = 0; i < rules.length(); i++) {
          JSONObject rule = rules.getJSONObject(i);
          String state = rule.getString("state");
          PendingIntent pending = alarm(context, rule);
          if (state.equals("scheduled")) {
            notifications.cancel(rule.getString("work_id"), 1);
            if (rule.getLong("due_at") <= now) {
              deliver(
                  context,
                  rule.getString("work_id"),
                  rule.getInt("revision"),
                  rule.getLong("due_at"));
            } else {
              if (preciseAllowed(context)) {
                try {
                  manager.setExactAndAllowWhileIdle(
                      AlarmManager.RTC_WAKEUP, rule.getLong("due_at"), pending);
                } catch (SecurityException denied) {
                  manager.setAndAllowWhileIdle(
                      AlarmManager.RTC_WAKEUP, rule.getLong("due_at"), pending);
                }
              } else
                manager.setAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP, rule.getLong("due_at"), pending);
            }
          } else {
            manager.cancel(pending);
            if (state.equals("posting")) {
              boolean active = false;
              for (android.service.notification.StatusBarNotification notification :
                  notifications.getActiveNotifications())
                active |= rule.getString("work_id").equals(notification.getTag());
              if (active)
                store.finishDelivery(
                    rule.getString("work_id"),
                    rule.getInt("revision"),
                    rule.getLong("due_at"),
                    now);
              else
                store.uncertainDelivery(
                    rule.getString("work_id"),
                    rule.getInt("revision"),
                    rule.getLong("due_at"),
                    now);
            } else if (state.matches("cancelled|dismissed|missed|blocked")) {
              notifications.cancel(rule.getString("work_id"), 1);
            }
          }
        }
      }
    }
  }

  public static void deliver(Context context, String id, int revision, long due) throws Exception {
    synchronized (LOCK) {
      try (MemoryStore memory = new MemoryStore(context)) {
        OrganizerStore store = new OrganizerStore(memory);
        long now = System.currentTimeMillis();
        if (!store.beginDelivery(id, revision, due, now, notificationsAllowed(context))) return;
        JSONObject item = store.work(id), rule = store.reminder(id);
        context
            .getSystemService(NotificationManager.class)
            .notify(id, 1, notification(context, item, rule));
        store.finishDelivery(id, revision, due, now);
      }
    }
  }

  private static PendingIntent action(Context context, JSONObject rule, String action)
      throws Exception {
    Intent intent =
        new Intent(context, ReminderReceiver.class)
            .setAction("com.mubai.infoinbox.ACTION")
            .setData(
                Uri.parse(
                    "infoinbox://action/"
                        + OrganizerStore.deliveryId(
                            rule.getString("work_id"),
                            rule.getInt("revision"),
                            rule.getLong("due_at"))
                        + "/"
                        + action))
            .putExtra("work_id", rule.getString("work_id"))
            .putExtra("revision", rule.getInt("revision"))
            .putExtra("due_at", rule.getLong("due_at"))
            .putExtra("choice", action);
    return PendingIntent.getBroadcast(
        context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
  }

  private static Notification notification(Context context, JSONObject item, JSONObject rule)
      throws Exception {
    NotificationManager manager = context.getSystemService(NotificationManager.class);
    NotificationChannel channel =
        new NotificationChannel(CHANNEL, "已确认事项提醒", NotificationManager.IMPORTANCE_DEFAULT);
    channel.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
    manager.createNotificationChannel(channel);
    Intent open =
        new Intent(context, OrganizerActivity.class)
            .putExtra("work_id", item.getString("id"))
            .setData(Uri.parse("infoinbox://work/" + item.getString("id")))
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
    PendingIntent content =
        PendingIntent.getActivity(
            context, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    Notification.Builder builder =
        new Notification.Builder(context, CHANNEL)
            .setSmallIcon(com.mubai.infoinbox.R.drawable.app_icon)
            .setContentTitle(item.getString("title"))
            .setContentText("打开查看出处，或选择完成、稍后提醒")
            .setContentIntent(content)
            .setDeleteIntent(action(context, rule, "dismiss"))
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setOnlyAlertOnce(true)
            .setAutoCancel(false)
            .addAction(
                new Notification.Action.Builder(null, "关闭提示", action(context, rule, "dismiss"))
                    .build())
            .addAction(
                new Notification.Action.Builder(null, "5 分钟后", action(context, rule, "snooze"))
                    .build())
            .addAction(
                new Notification.Action.Builder(null, "完成事项", action(context, rule, "complete"))
                    .build());
    return builder.build();
  }

  public static void acknowledge(Context context, String id, int revision, long due, String action)
      throws Exception {
    synchronized (LOCK) {
      try (MemoryStore memory = new MemoryStore(context)) {
        if (!new OrganizerStore(memory)
            .acknowledge(id, revision, due, action, System.currentTimeMillis())) return;
      }
      context.getSystemService(NotificationManager.class).cancel(id, 1);
      reconcile(context);
    }
  }

  public static String confirm(
      Context context, MemoryStore memory, JSONObject proposal, JSONObject edited, Long reminder)
      throws Exception {
    synchronized (LOCK) {
      String id =
          new OrganizerStore(memory)
              .confirm(proposal, edited, reminder, System.currentTimeMillis());
      reconcile(context);
      memory.scheduleKnowledge();
      return id;
    }
  }

  public static void changeState(Context context, MemoryStore memory, JSONObject item, String state)
      throws Exception {
    synchronized (LOCK) {
      new OrganizerStore(memory)
          .changeState(
              item.getString("id"), item.getInt("revision"), state, System.currentTimeMillis());
      reconcile(context);
      memory.scheduleKnowledge();
    }
  }
}
