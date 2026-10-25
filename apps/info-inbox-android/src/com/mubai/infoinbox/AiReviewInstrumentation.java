package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.ContentValues;
import android.database.Cursor;
import android.os.Bundle;
import org.json.JSONArray;
import org.json.JSONObject;

/** Debug-only checks in an isolated database. No real proposals or backend writes. */
public final class AiReviewInstrumentation extends Instrumentation {
  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    start();
  }

  private static JSONObject add(MemoryStore memory, String id, String kind, long now)
      throws Exception {
    String ref = "ai:" + AnalysisContract.hash(id);
    JSONObject source =
        new JSONObject()
            .put("account_id", "synthetic-review-owner")
            .put("talker", "synthetic-review-room")
            .put("msg_id", id.hashCode())
            .put("content", "合成测试资料")
            .put("created_at", now)
            .put("_memory_ref", ref);
    source.put("evidence", new JSONArray().put(new JSONObject(source.toString())));
    JSONObject item =
        new JSONObject()
            .put("review_ref", ref)
            .put("title", "合成建议 " + id)
            .put("kind", kind)
            .put("importance", "high")
            .put("reason", "本地隔离验收")
            .put("operation", "create")
            .put("timezone", "Asia/Shanghai")
            .put("state", "open")
            .put("involvement", "self")
            .put("source", source);
    ContentValues row = new ContentValues();
    row.put("ref", ref);
    row.put("title", item.getString("title"));
    row.put("kind", kind);
    row.put("importance", "high");
    row.put("reason", "本地隔离验收");
    row.put("item_json", item.toString());
    row.put("created_at", now);
    memory.getWritableDatabase().insertOrThrow("ai_reviews", null, row);
    return item;
  }

  private static int count(MemoryStore memory, String table) {
    try (Cursor c = memory.getReadableDatabase().rawQuery("SELECT count(*) FROM " + table, null)) {
      c.moveToFirst();
      return c.getInt(0);
    }
  }

  private static String state(MemoryStore memory, JSONObject item) throws Exception {
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

  @Override
  public void onStart() {
    String name = "review-acceptance-" + System.nanoTime() + ".db";
    Bundle out = new Bundle();
    boolean passed = false;
    long now = System.currentTimeMillis();
    try {
      JSONObject memo, todo;
      try (MemoryStore memory = new MemoryStore(getTargetContext(), name)) {
        memo = add(memory, "memory", "decision", now);
        todo = add(memory, "todo", "todo", now);
        JSONArray prepared =
            AiReviewBatch.preview(memory, new JSONArray().put(memo).put(todo), "Asia/Shanghai");
        if (AiReviewBatch.confirm(memory, prepared, now) != 2
            || count(memory, "memories") != 1
            || count(memory, "work_items") != 1
            || count(memory, "reminders") != 0) throw new AssertionError();
        out.putBoolean("mixed_save_without_new_reminders", true);
      }
      try (MemoryStore memory = new MemoryStore(getTargetContext(), name)) {
        if (!state(memory, memo).equals("handled") || !state(memory, todo).equals("handled"))
          throw new AssertionError();
        out.putBoolean("saved_after_reopen", true);
        JSONObject first = add(memory, "ignore-first", "reference", now),
            second = add(memory, "ignore-second", "fact", now);
        AiReviewBatch.ignore(memory, new JSONArray().put(first).put(second));
        if (!state(memory, first).equals("ignored")
            || !state(memory, second).equals("ignored")
            || count(memory, "memories") != 1
            || count(memory, "work_items") != 1) throw new AssertionError();
        out.putBoolean("ignore_preserves_confirmed_records", true);
        JSONObject valid = add(memory, "rollback-valid", "fact", now),
            invalid = add(memory, "rollback-invalid", "todo", now);
        JSONArray batch =
            AiReviewBatch.preview(memory, new JSONArray().put(valid).put(invalid), "Asia/Shanghai");
        batch
            .getJSONObject(1)
            .getJSONObject("edited")
            .put("title", new String(new char[201]).replace('\0', '长'));
        try {
          AiReviewBatch.confirm(memory, batch, now);
          throw new AssertionError();
        } catch (IllegalArgumentException expected) {
        }
        if (!state(memory, valid).equals("pending")
            || !state(memory, invalid).equals("pending")
            || count(memory, "memories") != 1
            || count(memory, "work_items") != 1) throw new AssertionError();
        out.putBoolean("atomic_failure_rolls_back", true);
        passed = true;
      }
    } catch (Throwable failure) {
      out.putString("error_type", failure.getClass().getSimpleName());
    } finally {
      getTargetContext().deleteDatabase(name);
    }
    out.putBoolean("passed", passed);
    finish(passed ? -1 : 0, out);
  }
}
