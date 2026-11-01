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
    enqueue(store, path, payload, label, true);
  }

  private static void enqueue(KnowledgeStore store, String path, JSONObject payload, String label,
      boolean wake) throws Exception {
    if (!(path.startsWith("/v1/agent/intents/") || path.startsWith("/v1/agent/review/")
        || path.startsWith("/v1/personal-assistant/work-items/")))
      throw new IllegalArgumentException("Unsupported action");
    try (Cursor count = store.db.rawQuery(
        "SELECT count(*) FROM runtime_actions WHERE space=? AND state IN ('pending','conflict')", new String[] {store.space})) {
      if (count.moveToFirst() && count.getInt(0) >= 1000)
        throw new IllegalStateException("待同步修改已达上限，请先联网同步或处理冲突");
    }
    String operation = UUID.randomUUID().toString();
    payload.put("operation_id", operation);
    store.db.execSQL("INSERT INTO runtime_actions(space,operation,path,payload,label) VALUES(?,?,?,?,?)",
        new Object[] {store.space, operation, path, payload.toString(), label});
    if (wake) store.memory.scheduleKnowledge();
  }

  static JSONArray list(KnowledgeStore store) throws Exception {
    return list(store, false);
  }

  private static JSONArray list(KnowledgeStore store, boolean pendingOnly) throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor rows = store.db.rawQuery(
        "SELECT operation,path,payload,label,state FROM runtime_actions WHERE space=?"
            + (pendingOnly ? " AND state='pending'" : " AND state IN ('pending','conflict')") + " ORDER BY rowid LIMIT 100",
        new String[] {store.space})) {
      while (rows.moveToNext()) result.put(new JSONObject()
          .put("operation", rows.getString(0)).put("path", rows.getString(1))
          .put("payload", new JSONObject(rows.getString(2)))
          .put("label", rows.getString(3)).put("state", rows.getString(4)));
    }
    return result;
  }

  static void ownerConfirmed(MemoryStore memory, String localId, int revision) throws Exception {
    BackendSettings backend = new BackendSettings(memory.applicationContext());
    if (backend.base().isEmpty() || backend.session().isEmpty()) return;
    KnowledgeStore store = new KnowledgeStore(memory, backend.base());
    try (Cursor known = store.db.rawQuery(
        "SELECT payload FROM knowledge_shadow WHERE space=? AND local_id=? AND revision>0 LIMIT 1",
        new String[] {store.space, localId})) {
      if (known.moveToFirst() && new JSONObject(known.getString(0)).getJSONObject("origin")
          .optBoolean("external_bound")) return; // Existing bound edits already use commands.
    }
    enqueue(store, "/v1/personal-assistant/work-items/review-confirmed",
        new JSONObject().put("_native_local_id", localId).put("_native_work_revision", revision),
        "确认事项并交给宁宁继续处理", false);
  }

  static void flush(KnowledgeStore store, RuntimeBackendClient client, BackendSettings backend)
      throws Exception {
    JSONArray actions = list(store, true);
    for (int i = 0; i < actions.length(); i++) {
      JSONObject action = actions.getJSONObject(i);
      if (!action.getString("state").equals("pending")) continue;
      try {
        JSONObject payload = action.getJSONObject("payload");
        if (payload.has("_native_local_id")) {
          if (!store.meta("owner_review_supported", "false").equals("true")) continue;
          JSONObject work = new OrganizerStore(store.memory).work(payload.getString("_native_local_id"));
          if (work == null || work.optString("state").matches("completed|cancelled")) {
            discard(store, action.getString("operation"));
            continue;
          }
          JSONObject local = store.localRecord(payload.getString("_native_local_id"), "todo");
          if (local == null) continue; // Original evidence must finish synchronizing first.
          if (local.getJSONObject("origin").getInt("native_work_revision")
              != payload.getInt("_native_work_revision")) {
            store.db.execSQL("UPDATE runtime_actions SET state='conflict' WHERE space=? AND operation=?",
                new Object[] {store.space, action.getString("operation")});
            continue;
          }
          JSONObject known = store.shadow(local.getString("record_id"));
          if (known == null || known.getLong("revision") == 0) continue;
          JSONObject record = new JSONObject(known.getString("payload"));
          if (record.getJSONObject("origin").optInt("native_work_revision", -1)
              != payload.getInt("_native_work_revision")) continue;
          payload = new JSONObject().put("operation_id", action.getString("operation"))
              .put("record_id", record.getString("record_id"))
              .put("expected_record_revision", known.getLong("revision"));
        }
        payload.remove("_replaces_operation");
        client.request(action.getString("path"), payload.put("session_id", backend.session()));
        discard(store, action.getString("operation"));
      } catch (RuntimeBackendClient.StateConflict changed) {
        store.db.execSQL("UPDATE runtime_actions SET state='conflict' WHERE space=? AND operation=?",
            new Object[] {store.space, action.getString("operation")});
      }
    }
  }

  static void rebase(KnowledgeStore store, String operation, int revision) throws Exception {
    store.db.beginTransactionNonExclusive();
    try {
      JSONObject item;
      try(Cursor row=store.db.rawQuery("SELECT path,payload,label FROM runtime_actions WHERE space=? AND operation=? AND state='conflict'",
          new String[]{store.space,operation})) {
        if(!row.moveToFirst())throw new IllegalStateException("本机修改状态已变化，请刷新");
        item=new JSONObject().put("path",row.getString(0)).put("payload",new JSONObject(row.getString(1)))
            .put("label",row.getString(2));
      }
      JSONObject payload=item.getJSONObject("payload");
      payload.put("_replaces_operation",operation);
      if(payload.has("_native_local_id")) {
        JSONObject work=new OrganizerStore(store.memory).work(payload.getString("_native_local_id"));
        if(work==null)throw new IllegalStateException("本机事项已撤回");
        payload.put("_native_work_revision",work.getInt("revision"));
      } else payload.put("expected_revision",revision);
      store.db.execSQL("UPDATE runtime_actions SET state='superseded' WHERE space=? AND operation=?",
          new Object[]{store.space,operation});
      enqueue(store,item.getString("path"),payload,item.getString("label"),false);
      store.db.setTransactionSuccessful();
    } finally {store.db.endTransaction();}
    store.memory.scheduleKnowledge();
  }

  static void discard(KnowledgeStore store, String operation) {
    store.db.execSQL("DELETE FROM runtime_actions WHERE space=? AND operation=?",
        new Object[] {store.space, operation});
  }
}
