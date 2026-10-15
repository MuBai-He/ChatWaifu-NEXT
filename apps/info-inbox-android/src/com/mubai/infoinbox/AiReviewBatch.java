package com.mubai.infoinbox;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.util.LinkedHashSet;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** A reviewed batch commits atomically using existing provenance and confirmation checks. */
public final class AiReviewBatch {
  private AiReviewBatch() {}

  private static void validate(JSONArray items) throws Exception {
    if (items.length() < 1 || items.length() > AiReviewSelection.PAGE_SIZE)
      throw new IllegalArgumentException("请选择 1–100 条建议");
    Set<String> refs = new LinkedHashSet<>();
    for (int i = 0; i < items.length(); i++)
      if (!refs.add(AiReviewSelection.key(items.getJSONObject(i))))
        throw new IllegalArgumentException("选中了重复建议，请重新选择");
  }

  private static JSONObject current(MemoryStore memory, JSONObject selected) throws Exception {
    try (Cursor c =
        memory
            .getReadableDatabase()
            .rawQuery(
                "SELECT state,item_json FROM ai_reviews WHERE ref=?",
                new String[] {AiReviewSelection.key(selected)})) {
      if (!c.moveToFirst()
          || !c.getString(0).equals("pending")
          || !new JSONObject(c.getString(1)).toString().equals(selected.toString()))
        throw new IllegalStateException("建议已变化或已处理，请刷新后重新选择");
      JSONObject proposal = new JSONObject(c.getString(1));
      new ConversationPolicy(memory).requireProposal(proposal);
      return proposal;
    }
  }

  public static int ignore(MemoryStore memory, JSONArray selected) throws Exception {
    validate(selected);
    synchronized (ConversationPolicy.SEND_LOCK) {
      SQLiteDatabase db = memory.getWritableDatabase();
      db.beginTransaction();
      try {
        for (int i = 0; i < selected.length(); i++) {
          if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
          JSONObject proposal = current(memory, selected.getJSONObject(i));
          ContentValues values = new ContentValues();
          values.put("state", "ignored");
          db.update(
              "ai_reviews",
              values,
              "ref=? AND state='pending'",
              new String[] {AiReviewSelection.key(proposal)});
        }
        db.setTransactionSuccessful();
      } finally {
        db.endTransaction();
      }
    }
    return selected.length();
  }

  public static String memoryKind(JSONObject proposal) {
    String kind = proposal.optString("kind");
    return kind.equals("decision") ? "决定" : kind.equals("reference") ? "资料" : "记忆";
  }

  private static Long proposed(JSONObject item, JSONObject old, String key) throws Exception {
    return item.has(key) && !item.isNull(key)
        ? TimeRules.parseIso(item.getString(key))
        : old == null ? null : OrganizerStore.nullable(old, key);
  }

  private static void time(JSONObject edited, String key, Long value) throws Exception {
    edited.put(key, value == null ? JSONObject.NULL : value);
  }

  /** Freeze the fields and existing reminder shown in the confirmation preview. */
  public static JSONArray preview(MemoryStore memory, JSONArray selected, String timezone)
      throws Exception {
    validate(selected);
    JSONArray result = new JSONArray();
    Set<String> targets = new LinkedHashSet<>();
    OrganizerStore store = new OrganizerStore(memory);
    SQLiteDatabase db = memory.getWritableDatabase();
    db.beginTransaction();
    try {
      for (int i = 0; i < selected.length(); i++) {
        JSONObject proposal = current(memory, selected.getJSONObject(i));
        store.verifySourceRevisions(proposal.getJSONObject("source"));
        JSONObject row = new JSONObject().put("proposal", proposal);
        String kind = proposal.getString("kind");
        if (kind.matches("todo|event")) {
          String op = proposal.optString("operation", "create");
          JSONObject old = op.equals("create") ? null : store.work(proposal.getString("target_id"));
          if (!op.equals("create")) {
            if (old == null || old.getInt("revision") != proposal.getInt("expected_revision"))
              throw new IllegalStateException("事项已被修改，请先单独核对这条建议");
            if (!targets.add(old.getString("id")))
              throw new IllegalStateException("多条建议涉及同一事项，请分别核对保存");
            row.put("previous_title", old.getString("title"));
          }
          JSONObject edited =
              new JSONObject()
                  .put("title", proposal.getString("title"))
                  .put(
                      "timezone",
                      old == null
                          ? proposal.optString("timezone", timezone)
                          : old.getString("timezone"))
                  .put(
                      "state",
                      old == null
                          ? proposal.optString("state", "open")
                          : old.optString("state", "open"))
                  .put(
                      "involvement",
                      old == null
                          ? proposal.optString("involvement", "unclear")
                          : old.getString("involvement"))
                  .put(
                      "location",
                      old == null
                          ? proposal.optString("location", "")
                          : old.optString("location", ""))
                  .put(
                      "details",
                      old == null
                          ? proposal.optString("details", "")
                          : old.optString("details", ""));
          time(edited, "due_at", kind.equals("event") ? null : proposed(proposal, old, "due_at"));
          time(edited, "starts_at", proposed(proposal, old, "starts_at"));
          time(edited, "ends_at", proposed(proposal, old, "ends_at"));
          TimeRules.validateRange(
              OrganizerStore.nullable(edited, "starts_at"),
              OrganizerStore.nullable(edited, "ends_at"));
          JSONObject reminder = old == null ? null : store.reminder(old.getString("id"));
          row.put("edited", edited)
              .put(
                  "remind_at",
                  !op.matches("complete|cancel")
                          && reminder != null
                          && reminder.optString("state").equals("scheduled")
                      ? reminder.getLong("due_at")
                      : JSONObject.NULL);
        } else {
          row.put("memory_kind", memoryKind(proposal));
        }
        result.put(row);
      }
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
    return result;
  }

  public static int confirm(MemoryStore memory, JSONArray prepared, long now) throws Exception {
    JSONArray selected = new JSONArray();
    for (int i = 0; i < prepared.length(); i++)
      selected.put(prepared.getJSONObject(i).getJSONObject("proposal"));
    validate(selected);
    synchronized (ReminderScheduler.LOCK) {
      synchronized (ConversationPolicy.SEND_LOCK) {
        SQLiteDatabase db = memory.getWritableDatabase();
        OrganizerStore store = new OrganizerStore(memory);
        db.beginTransaction();
        try {
          for (int i = 0; i < prepared.length(); i++) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
            JSONObject row = prepared.getJSONObject(i);
            JSONObject proposal = current(memory, row.getJSONObject("proposal"));
            if (proposal.getString("kind").matches("todo|event"))
              store.confirm(
                  proposal,
                  row.getJSONObject("edited"),
                  OrganizerStore.nullable(row, "remind_at"),
                  now);
            else
              store.confirmMemory(
                  proposal, proposal.getString("title"), row.getString("memory_kind"));
          }
          db.setTransactionSuccessful();
        } finally {
          db.endTransaction();
        }
      }
    }
    memory.scheduleKnowledge();
    return selected.length();
  }
}
