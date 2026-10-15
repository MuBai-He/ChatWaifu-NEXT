package com.mubai.infoinbox;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import org.json.JSONArray;
import org.json.JSONObject;

/** AI-only exclusions. The Root collector and original archive remain independent. */
public final class ConversationPolicy {
  // Serialize policy commits with the start of uploads, never with response/model waiting.
  static final Object SEND_LOCK = new Object();
  private final MemoryStore memory;

  public ConversationPolicy(MemoryStore memory) {
    this.memory = memory;
  }

  static void createTable(SQLiteDatabase db) {
    db.execSQL(
        "CREATE TABLE conversation_exclusions(account_id TEXT NOT NULL,talker TEXT NOT NULL,"
            + "title TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(account_id,talker))");
  }

  public boolean excluded(JSONObject source) {
    try (Cursor c =
        memory
            .getReadableDatabase()
            .rawQuery(
                "SELECT 1 FROM conversation_exclusions WHERE account_id=? AND talker=?",
                new String[] {source.optString("account_id"), source.optString("talker")})) {
      return c.moveToFirst();
    }
  }

  public JSONArray list() throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c =
        memory
            .getReadableDatabase()
            .rawQuery(
                "SELECT account_id,talker,title FROM conversation_exclusions ORDER BY created_at"
                    + " DESC",
                null)) {
      while (c.moveToNext())
        result.put(
            new JSONObject()
                .put("account_id", c.getString(0))
                .put("talker", c.getString(1))
                .put("title", c.getString(2)));
    }
    return result;
  }

  public int count() {
    try (Cursor c =
        memory
            .getReadableDatabase()
            .rawQuery("SELECT count(*) FROM conversation_exclusions", null)) {
      c.moveToFirst();
      return c.getInt(0);
    }
  }

  public void set(JSONObject room, boolean exclude) throws Exception {
    setMany(new JSONArray().put(room), exclude);
  }

  /** Validate the entire selection before one atomic policy/queue transaction. */
  public int setMany(JSONArray rooms, boolean exclude) throws Exception {
    if (rooms.length() < 1 || rooms.length() > 100)
      throw new IllegalArgumentException("请选择 1–100 个会话");
    java.util.Map<String, JSONObject> unique = new java.util.LinkedHashMap<>();
    for (int i = 0; i < rooms.length(); i++) {
      JSONObject room = new JSONObject(rooms.getJSONObject(i).toString());
      unique.put(ConversationSelection.key(room), room);
    }
    synchronized (SEND_LOCK) {
      SQLiteDatabase db = memory.getWritableDatabase();
      db.beginTransaction();
      try {
        for (JSONObject room : unique.values()) {
          apply(db, room, exclude);
          KnowledgeStore.policyChanged(db, room, exclude);
        }
        db.setTransactionSuccessful();
      } finally {
        db.endTransaction();
      }
    }
    memory.scheduleKnowledge();
    return unique.size();
  }

  void apply(SQLiteDatabase db, JSONObject room, boolean exclude) throws Exception {
    String account = room.getString("account_id"), talker = room.getString("talker");
    if (exclude) {
      db.execSQL(
          "INSERT OR REPLACE INTO conversation_exclusions VALUES(?,?,?,?)",
          new Object[] {
            account, talker, MessagePresentation.conversationTitle(room), System.currentTimeMillis()
          });
      // Cancel the whole affected batch, returning its other rooms to the queue. This also
      // fences exclude -> restore while a previously sent response is still in flight.
      db.execSQL(
          "UPDATE analysis_runs SET"
              + " state='excluded',lease_until=0,input_json='[]',error_code='conversation_excluded'"
              + " WHERE state='running' AND id IN (SELECT q.run_id FROM queued_sources q JOIN"
              + " source_snapshots s ON s.base_ref=q.base_ref WHERE s.account_id=? AND"
              + " s.talker=? AND q.state='leased')",
          new Object[] {account, talker});
      db.execSQL(
          "UPDATE queued_sources SET state='pending',run_id=NULL,next_attempt=0 WHERE"
              + " state='leased' AND run_id IN (SELECT id FROM analysis_runs WHERE"
              + " state='excluded')");
      db.execSQL(
          "UPDATE queued_sources SET state='excluded',run_id=NULL WHERE state IN"
              + " ('pending','failed') AND base_ref IN (SELECT base_ref FROM source_snapshots"
              + " WHERE account_id=? AND talker=?)",
          new Object[] {account, talker});
    } else {
      db.delete(
          "conversation_exclusions", "account_id=? AND talker=?", new String[] {account, talker});
      db.execSQL(
          "UPDATE queued_sources SET state='pending',retries=0,next_attempt=0,run_id=NULL "
              + "WHERE state='excluded' AND base_ref IN "
              + "(SELECT base_ref FROM source_snapshots WHERE account_id=? AND talker=?)",
          new Object[] {account, talker});
    }
  }

  /** Pending AI proposals are hidden while excluded; explicit local edits remain available. */
  public boolean allowsProposal(JSONObject item) throws Exception {
    if (item.optBoolean("manual")) return true;
    JSONObject source = item.getJSONObject("source");
    if (excluded(source)) return false;
    JSONArray evidence = source.optJSONArray("evidence");
    if (evidence != null)
      for (int i = 0; i < evidence.length(); i++)
        if (excluded(evidence.getJSONObject(i))) return false;
    return true;
  }

  public void requireProposal(JSONObject item) throws Exception {
    if (!allowsProposal(item)) throw new IllegalStateException("该会话已排除 AI 整理，请恢复后再核对建议");
  }

  public AnalysisSendGate gate(OrganizerStore organizer, JSONObject batch) {
    return write -> {
      synchronized (SEND_LOCK) {
        organizer.requireActiveBatch(batch, System.currentTimeMillis());
        write.run();
      }
    };
  }
}
