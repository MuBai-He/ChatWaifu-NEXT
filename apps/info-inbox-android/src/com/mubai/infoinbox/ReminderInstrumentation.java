package com.mubai.infoinbox;

import android.app.AlarmManager;
import android.app.Instrumentation;
import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.net.Uri;
import android.os.Bundle;
import android.service.notification.StatusBarNotification;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONArray;
import org.json.JSONObject;

/** Debug-only OS alarm/action acceptance with narrowly journalled synthetic rows. */
public final class ReminderInstrumentation extends Instrumentation {
  private Context context;
  private MemoryStore memory;
  private OrganizerStore store;
  private SharedPreferences journal;
  private final List<String> ids = new ArrayList<>();
  private final List<String> refs = new ArrayList<>();
  private int checks;

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    start();
  }

  private void check(boolean result, String name) {
    if (!result) throw new AssertionError(name);
    checks++;
  }

  private interface Condition {
    boolean ready() throws Exception;
  }

  private void await(Condition condition, long seconds, String name) throws Exception {
    CountDownLatch done = new CountDownLatch(1);
    AtomicReference<Throwable> failure = new AtomicReference<>();
    ScheduledExecutorService observer = Executors.newSingleThreadScheduledExecutor();
    observer.scheduleAtFixedRate(
        () -> {
          try {
            if (condition.ready()) done.countDown();
          } catch (Throwable error) {
            failure.set(error);
            done.countDown();
          }
        },
        0,
        200,
        TimeUnit.MILLISECONDS);
    boolean reached;
    try {
      reached = done.await(seconds, TimeUnit.SECONDS);
    } finally {
      observer.shutdownNow();
      observer.awaitTermination(5, TimeUnit.SECONDS);
    }
    if (failure.get() != null) throw new AssertionError(name, failure.get());
    check(reached, name);
  }

  private StatusBarNotification active(String id) {
    for (StatusBarNotification item :
        context.getSystemService(NotificationManager.class).getActiveNotifications()) {
      if (id.equals(item.getTag())) return item;
    }
    return null;
  }

  private void send(PendingIntent action) throws Exception {
    CountDownLatch finished = new CountDownLatch(1);
    action.send(
        context,
        0,
        null,
        (pending, intent, code, data, extras) -> finished.countDown(),
        new android.os.Handler(context.getMainLooper()));
    check(finished.await(10, TimeUnit.SECONDS), "notification action broadcast finished");
  }

  private String stage(int number, long due, String account) throws Exception {
    String title = "提醒验收 " + number + "（临时测试）";
    JSONObject source =
        new JSONObject()
            .put("account_id", account)
            .put("talker", "synthetic-reminder-room")
            .put("conversation_title", "合成提醒验收")
            .put("msg_id", number)
            .put("type", 1)
            .put("is_send", 1)
            .put("created_at", System.currentTimeMillis())
            .put("content", "合成测试事项，不含微信内容");
    JSONObject proposal = store.manual(source, "todo", title);
    String ref = proposal.getString("review_ref");
    String id = "work:" + AnalysisContract.hash(ref);
    refs.add(ref);
    ids.add(id);
    if (!journal
        .edit()
        .putString("ids", new JSONArray(ids).toString())
        .putString("refs", new JSONArray(refs).toString())
        .commit()) throw new IllegalStateException("fixture journal");
    JSONObject edited =
        new JSONObject()
            .put("title", title)
            .put("timezone", "Asia/Shanghai")
            .put("state", "open")
            .put("involvement", "self")
            .put("due_at", due)
            .put("starts_at", JSONObject.NULL)
            .put("ends_at", JSONObject.NULL);
    check(
        id.equals(ReminderScheduler.confirm(context, memory, proposal, edited, due)),
        "confirmed synthetic item");
    return id;
  }

  private void cleanup() throws Exception {
    synchronized (ReminderScheduler.LOCK) {
      JSONArray savedIds = new JSONArray(journal.getString("ids", "[]"));
      JSONArray savedRefs = new JSONArray(journal.getString("refs", "[]"));
      AlarmManager alarms = context.getSystemService(AlarmManager.class);
      NotificationManager notifications = context.getSystemService(NotificationManager.class);
      SQLiteDatabase db = memory.getWritableDatabase();
      for (int i = 0; i < savedIds.length(); i++) {
        String id = savedIds.getString(i);
        Intent alarm =
            new Intent(context, ReminderReceiver.class)
                .setAction("com.mubai.infoinbox.REMIND")
                .setData(Uri.parse("infoinbox://reminder/" + id));
        PendingIntent pending =
            PendingIntent.getBroadcast(
                context, 0, alarm, PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
        if (pending != null) {
          alarms.cancel(pending);
          pending.cancel();
        }
        notifications.cancel(id, 1);
      }
      db.beginTransaction();
      try {
        for (int i = 0; i < savedIds.length(); i++) {
          String[] id = {savedIds.getString(i)};
          db.delete("delivery_history", "work_id=?", id);
          db.delete("reminders", "work_id=?", id);
          db.delete("work_changes", "work_id=?", id);
          db.delete("work_items", "id=?", id);
        }
        for (int i = 0; i < savedRefs.length(); i++)
          db.delete("ai_reviews", "ref=?", new String[] {savedRefs.getString(i)});
        db.setTransactionSuccessful();
      } finally {
        db.endTransaction();
      }
      if (!journal.edit().clear().commit()) throw new IllegalStateException("fixture cleanup");
    }
  }

  private String retainedBusinessFingerprint() throws Exception {
    StringBuilder rows = new StringBuilder();
    for (String table : new String[] {"memories", "source_states", "ai_reviews", "work_items"}) {
      rows.append(table).append('\n');
      try (Cursor cursor =
          memory.getReadableDatabase().rawQuery("SELECT * FROM " + table + " ORDER BY 1", null)) {
        while (cursor.moveToNext()) rows.append(OrganizerStore.row(cursor)).append('\n');
      }
    }
    return AnalysisContract.hash(rows.toString());
  }

  @Override
  public void onStart() {
    Bundle output = new Bundle();
    boolean passed = false;
    String before = null;
    context = getTargetContext();
    journal = context.getSharedPreferences("reminder_acceptance_fixture", Context.MODE_PRIVATE);
    memory = new MemoryStore(context);
    store = new OrganizerStore(memory);
    try {
      cleanup();
      before = retainedBusinessFingerprint();
      check(ReminderScheduler.notificationsAllowed(context), "notification permission");
      check(ReminderScheduler.preciseAllowed(context), "exact alarm permission");
      long due = System.currentTimeMillis() + 15000L;
      String account = "reminder-fixture-" + UUID.randomUUID();
      String dismiss = stage(1, due, account);
      String snooze = stage(2, due, account);
      String complete = stage(3, due, account);
      String cancelled = stage(4, due, account);
      ReminderScheduler.changeState(context, memory, store.work(cancelled), "cancelled");
      check(System.currentTimeMillis() < due, "scheduled before deadline");
      check(
          active(dismiss) == null && active(snooze) == null && active(complete) == null,
          "not early");
      Bundle progress = new Bundle();
      progress.putString("phase", "waiting_for_system_alarm");
      sendStatus(1, progress);
      await(
          () -> {
            for (String id : new String[] {dismiss, snooze, complete}) {
              if (active(id) == null || !store.reminder(id).getString("state").equals("posted"))
                return false;
            }
            return true;
          },
          90,
          "AlarmManager and receiver posted all three notifications");
      long latest = 0;
      for (String id : new String[] {dismiss, snooze, complete}) {
        StatusBarNotification notice = active(id);
        check(notice.getPostTime() >= due, "system notification not before deadline");
        latest = Math.max(latest, notice.getPostTime() - due);
        check(notice.getNotification().actions.length == 3, "three real notification actions");
      }
      check(active(cancelled) == null, "cancelled alarm did not notify");
      Notification dismissNotice = active(dismiss).getNotification();
      Notification snoozeNotice = active(snooze).getNotification();
      Notification completeNotice = active(complete).getNotification();
      send(dismissNotice.actions[0].actionIntent);
      await(
          () ->
              active(dismiss) == null
                  && store.reminder(dismiss).getString("state").equals("dismissed"),
          10,
          "close button dismissed notification");
      check(store.work(dismiss).getString("state").equals("open"), "closing did not complete task");
      long snoozeFrom = System.currentTimeMillis();
      send(snoozeNotice.actions[1].actionIntent);
      await(
          () ->
              active(snooze) == null
                  && store.reminder(snooze).getString("state").equals("scheduled"),
          10,
          "snooze button scheduled new occurrence");
      long nextDue = store.reminder(snooze).getLong("due_at");
      check(
          nextDue >= snoozeFrom + 300000L && nextDue <= System.currentTimeMillis() + 300000L,
          "five-minute snooze deadline");
      send(snoozeNotice.actions[2].actionIntent);
      check(
          store.reminder(snooze).getLong("due_at") == nextDue
              && store.work(snooze).getString("state").equals("open"),
          "old notification action cannot complete new occurrence");
      send(completeNotice.actions[2].actionIntent);
      await(
          () ->
              active(complete) == null
                  && store.work(complete).getString("state").equals("completed"),
          10,
          "complete button completed task");
      check(store.work(complete).getInt("revision") == 2, "completion bumped revision once");
      output.putLong("latest_notification_delay_ms", latest);
      output.putBoolean("notifications", true);
      output.putBoolean("precise_reminders", true);
      passed = true;
    } catch (Throwable error) {
      output.putString("failure", error.getClass().getSimpleName() + ": " + error.getMessage());
    } finally {
      try {
        cleanup();
        if (before != null)
          check(before.equals(retainedBusinessFingerprint()), "user business rows preserved");
        for (String id : ids)
          check(
              store.work(id) == null && active(id) == null,
              "synthetic row and notification removed");
      } catch (Throwable error) {
        passed = false;
        output.putString(
            "cleanup_failure", error.getClass().getSimpleName() + ": " + error.getMessage());
      }
      memory.close();
    }
    output.putInt("assertions", checks);
    output.putString("result", passed ? "passed" : "failed");
    finish(passed ? -1 : 0, output);
  }
}
