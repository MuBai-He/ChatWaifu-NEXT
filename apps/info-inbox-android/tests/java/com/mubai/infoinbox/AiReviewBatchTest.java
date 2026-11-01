package com.mubai.infoinbox;

import static org.junit.Assert.*;

import android.app.Application;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 33, application = Application.class)
public class AiReviewBatchTest {
  @org.robolectric.annotation.Implements(className = "android.app.JobSchedulerImpl", isInAndroidSdk = false)
  public static class ObservingJobs extends org.robolectric.shadows.ShadowJobScheduler.ShadowJobSchedulerImpl {
    static MemoryStore owner;
    static int immediate;
    static boolean duringWrite;

    @Override @org.robolectric.annotation.Implementation
    public int schedule(android.app.job.JobInfo job) {
      if (job.getId() == 8811 && owner != null) {
        immediate++;
        duringWrite |= owner.getWritableDatabase().inTransaction();
      }
      return super.schedule(job);
    }
  }
  private Context context;
  private MemoryStore memory;
  private String name;
  private static final long NOW = System.currentTimeMillis();

  @Before
  public void setup() {
    context = RuntimeEnvironment.getApplication();
    name = "review-batch-" + UUID.randomUUID() + ".db";
    memory = new MemoryStore(context, name);
  }

  @After
  public void cleanup() {
    memory.close();
    context.deleteDatabase(name);
  }

  private JSONObject proposal(String id, String kind) throws Exception {
    String ref = "ai:" + AnalysisContract.hash(id);
    JSONObject source =
        new JSONObject()
            .put("account_id", "fixture-owner")
            .put("talker", "fixture-room")
            .put("msg_id", id.hashCode())
            .put("content", "合成原文")
            .put("created_at", NOW)
            .put("_memory_ref", ref);
    source.put("evidence", new JSONArray().put(new JSONObject(source.toString())));
    JSONObject item =
        new JSONObject()
            .put("review_ref", ref)
            .put("kind", kind)
            .put("title", "合成建议 " + id)
            .put("source", source)
            .put("importance", "high")
            .put("reason", "批量验收")
            .put("operation", "create")
            .put("timezone", "Asia/Shanghai")
            .put("involvement", "self")
            .put("state", "open");
    insert(item);
    return item;
  }

  private void insert(JSONObject item) throws Exception {
    ContentValues values = new ContentValues();
    values.put("ref", item.getString("review_ref"));
    values.put("title", item.getString("title"));
    values.put("kind", item.getString("kind"));
    values.put("importance", "high");
    values.put("reason", "批量验收");
    values.put("item_json", item.toString());
    values.put("created_at", NOW);
    memory
        .getWritableDatabase()
        .insertWithOnConflict(
            "ai_reviews", null, values, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE);
  }

  private int count(String table) {
    try (Cursor c = memory.getReadableDatabase().rawQuery("SELECT count(*) FROM " + table, null)) {
      c.moveToFirst();
      return c.getInt(0);
    }
  }

  private String state(JSONObject item) throws Exception {
    try (Cursor c =
        memory
            .getReadableDatabase()
            .rawQuery(
                "SELECT state FROM ai_reviews WHERE ref=?",
                new String[] {item.getString("review_ref")})) {
      c.moveToFirst();
      return c.getString(0);
    }
  }

  private JSONArray prepare(JSONObject... items) throws Exception {
    JSONArray selected = new JSONArray();
    for (JSONObject item : items) selected.put(item);
    return AiReviewBatch.preview(memory, selected, "Asia/Shanghai");
  }

  @Test
  public void mixedConfirmationSavesWorkAndMemoryOnceWithoutNewReminders() throws Exception {
    JSONObject memo = proposal("memory", "decision"), todo = proposal("todo", "todo");
    todo.put("due_at", "2026-10-20T10:00:00+08:00");
    insert(todo);
    JSONArray preview = prepare(memo, todo);
    assertEquals(0, count("memories"));
    assertEquals(0, count("work_items"));
    assertEquals(2, AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(1, count("memories"));
    assertEquals(1, count("work_items"));
    assertEquals(0, count("reminders"));
    assertEquals("handled", state(todo));
    assertEquals("handled", state(memo));
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(1, count("work_items"));
  }

  @Test
  public void confirmedWorkKeepsOneDurableRuntimeReviewAcrossReopen() throws Exception {
    String base = "https://fixture.invalid";
    context.getSharedPreferences("backend", Context.MODE_PRIVATE).edit()
        .putString("base", base).putString("owner_session", UUID.randomUUID().toString()).commit();
    JSONObject item = proposal("runtime-review", "event");
    JSONArray preview=prepare(item);
    assertEquals(1, AiReviewBatch.confirm(memory, preview, NOW));
    KnowledgeStore knowledge = new KnowledgeStore(memory, base);
    JSONArray queued = RuntimeActions.list(knowledge);
    assertEquals(1, queued.length());
    String operation = queued.getJSONObject(0).getString("operation");
    assertEquals("/v1/personal-assistant/work-items/review-confirmed",
        queued.getJSONObject(0).getString("path"));
    assertEquals(1, queued.getJSONObject(0).getJSONObject("payload")
        .getInt("_native_work_revision"));
    memory.close();
    memory = new MemoryStore(context, name);
    assertEquals(1, count("work_items"));
    assertEquals(operation, RuntimeActions.list(new KnowledgeStore(memory, base))
        .getJSONObject(0).getString("operation"));
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(1, RuntimeActions.list(new KnowledgeStore(memory, base)).length());
  }

  @Test
  public void reviewOutboxCapacityRollsBackTheWholeConfirmationBatch() throws Exception {
    String base = "https://fixture.invalid";
    context.getSharedPreferences("backend", Context.MODE_PRIVATE).edit()
        .putString("base", base).putString("owner_session", UUID.randomUUID().toString()).commit();
    KnowledgeStore knowledge = new KnowledgeStore(memory, base);
    memory.getWritableDatabase().beginTransactionNonExclusive();
    try {
      for (int i = 0; i < 999; i++) memory.getWritableDatabase().execSQL(
          "INSERT INTO runtime_actions(space,operation,path,payload,label) VALUES(?,?,?,?,?)",
          new Object[] {knowledge.space, "existing-" + i, "/v1/agent/intents/fixture", "{}", "fixture"});
      memory.getWritableDatabase().setTransactionSuccessful();
    } finally { memory.getWritableDatabase().endTransaction(); }
    JSONObject first = proposal("runtime-batch-first", "todo");
    JSONObject second = proposal("runtime-batch-second", "event");
    JSONArray preview = prepare(first, second);
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(0, count("work_items"));
    assertEquals(999, count("runtime_actions"));
    assertEquals("pending", state(first));
    assertEquals("pending", state(second));
  }

  @Test
  public void previewDoesNotWaitForTheWriterAndSiblingCloseKeepsTheDatabaseAlive() throws Exception {
    JSONObject item = proposal("read-only-preview", "fact");
    java.util.concurrent.ExecutorService threads = java.util.concurrent.Executors.newFixedThreadPool(2);
    java.util.concurrent.CountDownLatch writing = new java.util.concurrent.CountDownLatch(1),
        release = new java.util.concurrent.CountDownLatch(1);
    java.util.concurrent.Future<?> writer = threads.submit(() -> {
      try (MemoryStore sibling = new MemoryStore(context, name)) {
        android.database.sqlite.SQLiteDatabase db = sibling.getWritableDatabase();
        db.beginTransactionNonExclusive();
        try {
          writing.countDown();
          release.await();
          db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
      } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    });
    try {
      assertTrue(writing.await(5, java.util.concurrent.TimeUnit.SECONDS));
      java.util.concurrent.Future<JSONArray> preview = threads.submit(() -> prepare(item));
      assertEquals(1, preview.get(2, java.util.concurrent.TimeUnit.SECONDS).length());
    } finally {
      release.countDown();
      writer.get(5, java.util.concurrent.TimeUnit.SECONDS);
      threads.shutdownNow();
    }
    assertEquals(1, AiReviewBatch.confirm(memory, prepare(item), NOW));
    assertEquals(1, count("memories"));
    assertTrue(memory.getWritableDatabase().isOpen());
  }

  @Test
  public void maximumPageSaveIsAtomicAcrossOneHundredItems() throws Exception {
    JSONArray selected = new JSONArray();
    for (int i = 0; i < 100; i++) selected.put(proposal("bulk-" + i, "fact"));
    JSONArray prepared = AiReviewBatch.preview(memory, selected, "Asia/Shanghai");
    memory.getWritableDatabase().execSQL("CREATE TRIGGER reject_last BEFORE INSERT ON memories "
        + "WHEN NEW.title='合成建议 bulk-99' BEGIN SELECT RAISE(ABORT,'fixture_rollback'); END");
    assertThrows(android.database.sqlite.SQLiteException.class,
        () -> AiReviewBatch.confirm(memory, prepared, NOW));
    assertEquals(0, count("memories"));
    try (Cursor c = memory.getReadableDatabase().rawQuery(
        "SELECT count(*) FROM ai_reviews WHERE state='pending'", null)) {
      assertTrue(c.moveToFirst());
      assertEquals(100, c.getInt(0));
    }
    memory.getWritableDatabase().execSQL("DROP TRIGGER reject_last");
    long started = System.nanoTime();
    assertEquals(100, AiReviewBatch.confirm(memory, prepared, NOW));
    System.out.println("atomic_100_save_ms=" + (System.nanoTime() - started) / 1000000L);
    assertEquals(100, count("memories"));
    assertEquals(0, count("reminders"));
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, prepared, NOW));
    assertEquals(100, count("memories"));
  }

  @Test
  public void storageContentionDoesNotConsumeModelFailureRetries() throws Exception {
    JSONObject raw = new JSONObject(proposal("busy", "fact").getJSONObject("source").toString());
    raw.remove("_memory_ref");
    raw.remove("evidence");
    raw.put("type", 1).put("is_send", 1);
    OrganizerStore queue = new OrganizerStore(memory);
    queue.enqueue(new JSONArray().put(raw));
    JSONObject batch = queue.claim(NOW);
    assertNotNull(batch);
    queue.fail(batch.getString("id"), NOW + 1, true, "storage_busy");
    assertEquals(1, queue.queueStats().optInt("pending"));
    assertEquals(0, queue.queueStats().optInt("failed"));
    try (Cursor c = memory.getReadableDatabase().rawQuery("SELECT retries FROM queued_sources", null)) {
      assertTrue(c.moveToFirst());
      assertEquals(0, c.getInt(0));
    }
    assertEquals("storage_busy", queue.queueStats().getString("last_failure_code"));
  }

  @Test
  @Config(shadows = ObservingJobs.class)
  public void bulkSaveNotifiesOnceOnlyAfterTheOuterCommit() throws Exception {
    memory.close();
    name = "information.db";
    context.deleteDatabase(name);
    memory = new MemoryStore(context);
    context.getSharedPreferences("knowledge", Context.MODE_PRIVATE).edit().putBoolean("enabled", true).commit();
    JSONArray selected = new JSONArray();
    for (int i = 0; i < 100; i++) selected.put(proposal("notify-" + i, "fact"));
    JSONArray prepared = AiReviewBatch.preview(memory, selected, "Asia/Shanghai");
    ObservingJobs.owner = memory;
    ObservingJobs.immediate = 0;
    ObservingJobs.duringWrite = false;
    try {
      assertEquals(100, AiReviewBatch.confirm(memory, prepared, NOW));
      assertEquals(1, ObservingJobs.immediate);
      assertFalse(ObservingJobs.duringWrite);
    } finally {
      ObservingJobs.owner = null;
      context.getSharedPreferences("knowledge", Context.MODE_PRIVATE).edit().putBoolean("enabled", false).commit();
      context.getSystemService(android.app.job.JobScheduler.class).cancelAll();
    }
  }

  @Test
  public void failureRollsBackEveryMemoryWorkAndReviewInBatch() throws Exception {
    JSONObject first = proposal("first", "fact"), bad = proposal("bad", "todo");
    bad.put("title", new String(new char[201]).replace('\0', '长'));
    insert(bad);
    JSONArray preview = prepare(first, bad);
    assertThrows(IllegalArgumentException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(0, count("memories"));
    assertEquals(0, count("work_items"));
    assertEquals("pending", state(first));
    assertEquals("pending", state(bad));
  }

  @Test
  public void ignoreDoesNotTouchConfirmedRecordsAndRollsBackStaleSelection() throws Exception {
    JSONObject saved = proposal("saved", "fact");
    AiReviewBatch.confirm(memory, prepare(saved), NOW);
    JSONObject first = proposal("first", "reference"), second = proposal("second", "reference");
    memory.reviewState(second.getString("review_ref"), "handled");
    assertThrows(
        IllegalStateException.class,
        () -> AiReviewBatch.ignore(memory, new JSONArray().put(first).put(second)));
    assertEquals("pending", state(first));
    assertEquals(1, AiReviewBatch.ignore(memory, new JSONArray().put(first)));
    assertEquals("ignored", state(first));
    assertEquals(1, count("memories"));
  }

  @Test
  public void exclusionsAndChangedProposalBlockLateConfirmation() throws Exception {
    JSONObject first = proposal("first", "fact"), second = proposal("second", "fact");
    JSONArray preview = prepare(first, second);
    new ConversationPolicy(memory).set(first.getJSONObject("source"), true);
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(0, count("memories"));
    new ConversationPolicy(memory).set(first.getJSONObject("source"), false);
    second.put("title", "修订过的建议");
    insert(second);
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(0, count("memories"));
    assertEquals("pending", state(first));
  }

  @Test
  public void sourceRevisionChangeAfterPreviewCannotBeConfirmed() throws Exception {
    JSONObject memo = proposal("memo", "fact");
    JSONObject evidence = memo.getJSONObject("source").getJSONArray("evidence").getJSONObject(0);
    evidence.put("_base_ref", "fixture-base").put("_revision", "v1");
    insert(memo);
    memory
        .getWritableDatabase()
        .execSQL(
            "INSERT INTO source_snapshots"
                + " VALUES('fixture-base','fixture-owner',1,'fixture-room','合成原文','hash','v1')");
    JSONArray preview = prepare(memo);
    memory.getWritableDatabase().execSQL("UPDATE source_snapshots SET revision='v2'");
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(0, count("memories"));
    assertEquals("pending", state(memo));
  }

  @Test
  public void updatePreservesReminderAndCancelRemovesIt() throws Exception {
    OrganizerStore store = new OrganizerStore(memory);
    JSONObject todo = proposal("todo", "todo");
    JSONObject row = prepare(todo).getJSONObject(0);
    row.put("remind_at", NOW + 1000000);
    AiReviewBatch.confirm(memory, new JSONArray().put(row), NOW);
    String id = "work:" + AnalysisContract.hash(todo.getString("review_ref"));
    JSONObject update =
        proposal("update", "todo")
            .put("operation", "update")
            .put("target_id", id)
            .put("expected_revision", 1);
    insert(update);
    AiReviewBatch.confirm(memory, prepare(update), NOW);
    assertEquals(NOW + 1000000, store.reminder(id).getLong("due_at"));
    assertEquals(2, store.reminder(id).getInt("revision"));
    JSONObject cancel =
        proposal("cancel", "todo")
            .put("operation", "cancel")
            .put("target_id", id)
            .put("expected_revision", 2);
    insert(cancel);
    AiReviewBatch.confirm(memory, prepare(cancel), NOW);
    assertEquals("cancelled", store.work(id).getString("state"));
    assertEquals("cancelled", store.reminder(id).getString("state"));
  }

  @Test
  public void targetRevisionChangeAfterPreviewRollsBackWholeBatch() throws Exception {
    JSONObject todo = proposal("todo", "todo");
    AiReviewBatch.confirm(memory, prepare(todo), NOW);
    String id = "work:" + AnalysisContract.hash(todo.getString("review_ref"));
    JSONObject memo = proposal("memory", "fact");
    JSONObject update =
        proposal("update", "todo")
            .put("operation", "update")
            .put("target_id", id)
            .put("expected_revision", 1);
    insert(update);
    JSONArray preview = prepare(memo, update);
    new OrganizerStore(memory).changeState(id, 1, "waiting", NOW + 1);
    assertThrows(IllegalStateException.class, () -> AiReviewBatch.confirm(memory, preview, NOW));
    assertEquals(0, count("memories"));
    assertEquals("pending", state(memo));
    assertEquals("waiting", new OrganizerStore(memory).work(id).getString("state"));
  }
}
