package com.mubai.infoinbox;

import android.database.Cursor;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Device-owned edit outbox. Server revisions and operation receipts decide the result. */
final class RuntimeActions {
  private RuntimeActions() {}

  static void createTables(android.database.sqlite.SQLiteDatabase db) {
    db.execSQL("CREATE TABLE IF NOT EXISTS runtime_actions(space TEXT NOT NULL,operation TEXT NOT NULL,path TEXT NOT NULL,payload TEXT NOT NULL,label TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(space,operation))");
  }

  static void enqueue(KnowledgeStore store, String path, JSONObject payload, String label)
      throws Exception {
    if (!(path.startsWith("/v1/agent/intents/") || path.startsWith("/v1/agent/review/")
        || path.startsWith("/v1/personal-assistant/work-items/")))
      throw new IllegalArgumentException("Unsupported action");
    if (list(store).length() >= 100) throw new IllegalStateException("请先处理已有的待同步修改");
    String operation = UUID.randomUUID().toString();
    payload.put("operation_id", operation);
    store.db.execSQL("INSERT INTO runtime_actions(space,operation,path,payload,label) VALUES(?,?,?,?,?)",
        new Object[] {store.space, operation, path, payload.toString(), label});
    store.memory.scheduleKnowledge();
  }

  static JSONArray list(KnowledgeStore store) throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor rows = store.db.rawQuery(
        "SELECT operation,path,payload,label,state FROM runtime_actions WHERE space=? ORDER BY rowid LIMIT 100",
        new String[] {store.space})) {
      while (rows.moveToNext()) result.put(new JSONObject()
          .put("operation", rows.getString(0)).put("path", rows.getString(1))
          .put("payload", new JSONObject(rows.getString(2)))
          .put("label", rows.getString(3)).put("state", rows.getString(4)));
    }
    return result;
  }

  static void flush(KnowledgeStore store, RuntimeBackendClient client, BackendSettings backend)
      throws Exception {
    JSONArray actions = list(store);
    for (int i = 0; i < actions.length(); i++) {
      JSONObject action = actions.getJSONObject(i);
      if (!action.getString("state").equals("pending")) continue;
      try {
        client.request(action.getString("path"),
            action.getJSONObject("payload").put("session_id", backend.session()));
        discard(store, action.getString("operation"));
      } catch (RuntimeBackendClient.StateConflict changed) {
        store.db.execSQL("UPDATE runtime_actions SET state='conflict' WHERE space=? AND operation=?",
            new Object[] {store.space, action.getString("operation")});
      }
    }
  }

  static void discard(KnowledgeStore store, String operation) {
    store.db.execSQL("DELETE FROM runtime_actions WHERE space=? AND operation=?",
        new Object[] {store.space, operation});
  }
}
