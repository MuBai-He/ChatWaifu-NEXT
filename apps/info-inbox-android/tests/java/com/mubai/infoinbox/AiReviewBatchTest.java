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
  private Context context;
  private MemoryStore memory;
  private String name;
  private static final long NOW = 1791800000000L;

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
