package com.mubai.infoinbox;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Durable queue, proposals, work revisions and delivery state share one transaction boundary. */
public final class OrganizerStore {
  private final MemoryStore store;

  public OrganizerStore(MemoryStore store) {
    this.store = store;
  }

  static void createTables(SQLiteDatabase db) {
    db.execSQL(
        "CREATE TABLE source_snapshots(base_ref TEXT PRIMARY KEY,account_id TEXT NOT NULL,msg_id"
            + " INTEGER NOT NULL,talker TEXT NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT"
            + " NULL,revision TEXT NOT NULL,UNIQUE(account_id,msg_id))");
    db.execSQL(
        "CREATE TABLE queued_sources(ref TEXT PRIMARY KEY,base_ref TEXT NOT NULL,revision TEXT NOT"
            + " NULL,segment INTEGER NOT NULL,source_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT"
            + " 'pending',run_id TEXT,retries INTEGER NOT NULL DEFAULT 0,next_attempt INTEGER NOT"
            + " NULL DEFAULT 0,created_at INTEGER NOT NULL)");
    db.execSQL("CREATE INDEX queue_ready ON queued_sources(state,next_attempt,created_at)");
    db.execSQL(
        "CREATE TABLE analysis_runs(id TEXT PRIMARY KEY,state TEXT NOT NULL,lease_until INTEGER NOT"
            + " NULL,input_json TEXT NOT NULL,error_code TEXT,created_at INTEGER NOT NULL)");
    db.execSQL(
        "CREATE TABLE work_items(id TEXT PRIMARY KEY,kind TEXT NOT NULL,title TEXT NOT NULL,state"
            + " TEXT NOT NULL,involvement TEXT NOT NULL,location TEXT NOT NULL DEFAULT '',details"
            + " TEXT NOT NULL DEFAULT '',account_id TEXT NOT NULL,talker TEXT NOT NULL,due_at"
            + " INTEGER,starts_at INTEGER,ends_at INTEGER,timezone TEXT NOT NULL,revision INTEGER"
            + " NOT NULL,source_json TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER"
            + " NOT NULL)");
    db.execSQL(
        "CREATE TABLE work_changes(id TEXT PRIMARY KEY,work_id TEXT NOT NULL,operation TEXT NOT"
            + " NULL,revision INTEGER NOT NULL,source_json TEXT NOT NULL,created_at INTEGER NOT"
            + " NULL)");
    db.execSQL(
        "CREATE TABLE reminders(work_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,due_at INTEGER"
            + " NOT NULL,state TEXT NOT NULL,updated_at INTEGER NOT NULL)");
    db.execSQL(
        "CREATE TABLE delivery_history(id TEXT PRIMARY KEY,work_id TEXT NOT NULL,revision INTEGER"
            + " NOT NULL,due_at INTEGER NOT NULL,state TEXT NOT NULL,updated_at INTEGER NOT NULL)");
    db.execSQL("CREATE TABLE organizer_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)");
  }

  public long intakeFloor() {
    SQLiteDatabase db = store.getWritableDatabase();
    db.execSQL(
        "INSERT OR IGNORE INTO organizer_meta(key,value) VALUES('intake_floor',?)",
        new Object[] {Long.toString(System.currentTimeMillis() - 7L * 86400000L)});
    try (Cursor c =
        db.rawQuery("SELECT value FROM organizer_meta WHERE key='intake_floor'", null)) {
      c.moveToFirst();
      return Long.parseLong(c.getString(0));
    }
  }

  public void includeHistory() {
    store
        .getWritableDatabase()
        .execSQL("INSERT OR REPLACE INTO organizer_meta(key,value) VALUES('intake_floor','0')");
  }

  public int enqueue(JSONArray messages) throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    int added = 0;
    db.beginTransaction();
    try {
      for (int i = 0; i < messages.length(); i++) {
        JSONObject source = messages.getJSONObject(i);
        if (new ConversationPolicy(store).excluded(source)) continue;
        String base = MemoryStore.reference(source);
        String text = source.optString("content", "");
        String contentHash =
            AnalysisContract.hash(
                source.getInt("type")
                    + "\0"
                    + source.getInt("is_send")
                    + "\0"
                    + source.getLong("created_at")
                    + "\0"
                    + text);
        try (Cursor c =
            db.rawQuery(
                "SELECT content_hash FROM source_snapshots WHERE base_ref=?",
                new String[] {base})) {
          if (c.moveToFirst() && contentHash.equals(c.getString(0))) continue;
        }
        String revision = AnalysisContract.hash(contentHash + "\0" + UUID.randomUUID());
        ContentValues superseded = new ContentValues();
        superseded.put("state", "superseded");
        db.update("queued_sources", superseded, "base_ref=?", new String[] {base});
        ContentValues snapshot = new ContentValues();
        snapshot.put("base_ref", base);
        snapshot.put("account_id", source.getString("account_id"));
        snapshot.put("msg_id", source.getLong("msg_id"));
        snapshot.put("talker", source.getString("talker"));
        snapshot.put("content", text);
        snapshot.put("content_hash", contentHash);
        snapshot.put("revision", revision);
        db.insertWithOnConflict(
            "source_snapshots", null, snapshot, SQLiteDatabase.CONFLICT_REPLACE);
        int parts = Math.max(1, (text.length() + 2999) / 3000);
        int from = 0, part = 0;
        do {
          int to = Math.min(text.length(), from + 3000);
          if (to < text.length() && to > from && Character.isHighSurrogate(text.charAt(to - 1)))
            to--;
          JSONObject segment = new JSONObject(source.toString());
          String ref = base + ":" + revision + ":" + part;
          segment
              .put("content", text.substring(from, to))
              .put("_source_ref", ref)
              .put("_segment_prefix", text.substring(Math.max(0, from - 200), from))
              .put("_base_ref", base)
              .put("_revision", revision)
              .put("segment", part + 1)
              .put("segments_estimate", parts)
              .put("quote_truncated", parts > 1 ? 1 : 0);
          ContentValues v = new ContentValues();
          v.put("ref", ref);
          v.put("base_ref", base);
          v.put("revision", revision);
          v.put("segment", part);
          v.put("source_json", segment.toString());
          v.put("state", "pending");
          v.put("created_at", source.getLong("created_at"));
          db.insertOrThrow("queued_sources", null, v);
          added++;
          from = to;
          part++;
        } while (from < text.length());
      }
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
    return added;
  }

  public int pendingWithinScope(long floor) {
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT count(*) FROM queued_sources q JOIN source_snapshots s ON"
                    + " s.base_ref=q.base_ref WHERE q.state='pending' AND q.created_at>=? AND NOT"
                    + " EXISTS(SELECT 1 FROM conversation_exclusions e WHERE"
                    + " e.account_id=s.account_id AND e.talker=s.talker)",
                new String[] {Long.toString(floor)})) {
      c.moveToFirst();
      return c.getInt(0);
    }
  }

  public JSONObject claim(long now) throws Exception {
    return claim(now, 0);
  }

  public JSONObject claim(long now, long floor) throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      db.execSQL(
          "UPDATE queued_sources SET state='pending',run_id=NULL WHERE state='leased' AND run_id IN"
              + " (SELECT id FROM analysis_runs WHERE lease_until<=?)",
          new Object[] {now});
      db.execSQL(
          "UPDATE analysis_runs SET state='expired',input_json='[]' WHERE state='running' AND"
              + " lease_until<=?",
          new Object[] {now});
      try (Cursor c =
          db.rawQuery(
              "SELECT id FROM analysis_runs WHERE state='running' AND lease_until>? LIMIT 1",
              new String[] {Long.toString(now)})) {
        if (c.moveToFirst()) return null;
      }
      JSONArray sources = new JSONArray();
      int size = 0;
      try (Cursor c =
          db.rawQuery(
              "SELECT q.source_json FROM queued_sources q JOIN source_snapshots s ON"
                  + " s.base_ref=q.base_ref AND s.revision=q.revision WHERE q.state='pending' AND"
                  + " NOT EXISTS(SELECT 1 FROM conversation_exclusions e WHERE"
                  + " e.account_id=s.account_id AND e.talker=s.talker) AND q.next_attempt<=? AND"
                  + " q.created_at>=? ORDER BY q.created_at DESC,s.msg_id DESC,q.base_ref,q.segment"
                  + " LIMIT 30",
              new String[] {Long.toString(now), Long.toString(floor)})) {
        while (c.moveToNext()) {
          JSONObject source = new JSONObject(c.getString(0));
          int length = source.optString("content", "").length();
          if (size + length > 16000 && sources.length() > 0) break;
          sources.put(source);
          size += length;
        }
      }
      if (sources.length() == 0) return null;
      // Select recent records first, then present the selected cohort in message order.
      List<JSONObject> ordered = new ArrayList<>();
      for (int i = 0; i < sources.length(); i++) ordered.add(sources.getJSONObject(i));
      ordered.sort(
          (a, b) -> {
            int time = Long.compare(a.optLong("created_at"), b.optLong("created_at"));
            if (time != 0) return time;
            int message = Long.compare(a.optLong("msg_id"), b.optLong("msg_id"));
            if (message != 0) return message;
            int base = a.optString("_base_ref").compareTo(b.optString("_base_ref"));
            return base != 0 ? base : Integer.compare(a.optInt("segment"), b.optInt("segment"));
          });
      sources = new JSONArray(ordered);
      String id = UUID.randomUUID().toString();
      ContentValues run = new ContentValues();
      run.put("id", id);
      run.put("state", "running");
      run.put("lease_until", now + 6L * 60000L);
      run.put("input_json", sources.toString());
      run.put("created_at", now);
      db.insertOrThrow("analysis_runs", null, run);
      for (int i = 0; i < sources.length(); i++) {
        ContentValues v = new ContentValues();
        v.put("state", "leased");
        v.put("run_id", id);
        db.update(
            "queued_sources",
            v,
            "ref=? AND state='pending'",
            new String[] {MemoryStore.reference(sources.getJSONObject(i))});
      }
      db.setTransactionSuccessful();
      return new JSONObject().put("id", id).put("sources", sources);
    } finally {
      db.endTransaction();
    }
  }

  /** Call once at application process startup, before any worker can claim a batch. */
  public void recoverInterruptedRuns() {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      db.execSQL(
          "UPDATE queued_sources SET state='pending',run_id=NULL,next_attempt=0 WHERE"
              + " state='leased' AND run_id IN (SELECT id FROM analysis_runs WHERE"
              + " state='running')");
      db.execSQL(
          "UPDATE analysis_runs SET state='failed',lease_until=0,input_json='[]',"
              + " error_code='process_restarted' WHERE state='running'");
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public void finish(JSONObject batch, JSONObject response, long now) throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      String id = batch.getString("id");
      requireActiveBatch(batch, now);
      JSONArray input = batch.getJSONArray("sources");
      Map<String, JSONObject> allowed = new LinkedHashMap<>();
      for (int i = 0; i < input.length(); i++)
        allowed.put(MemoryStore.reference(input.getJSONObject(i)), input.getJSONObject(i));
      JSONArray processed = response.getJSONArray("processed_refs");
      if (processed.length() == 0) throw new IllegalArgumentException("批次没有处理进度");
      for (int i = 0; i < processed.length(); i++) {
        String ref = processed.getString(i);
        if (!allowed.containsKey(ref)) throw new IllegalArgumentException("批次来源不一致");
        try (Cursor c =
            db.rawQuery(
                "SELECT q.state,q.run_id,q.revision,s.revision FROM queued_sources q JOIN"
                    + " source_snapshots s ON s.base_ref=q.base_ref WHERE q.ref=?",
                new String[] {ref})) {
          if (!c.moveToFirst()
              || !c.getString(0).equals("leased")
              || !id.equals(c.getString(1))
              || !c.getString(2).equals(c.getString(3))) {
            throw new IllegalStateException("原消息已变化，结果未提交");
          }
        }
      }
      JSONArray items = response.getJSONArray("items");
      for (int i = 0; i < items.length(); i++) {
        JSONObject item = items.getJSONObject(i);
        ContentValues v = new ContentValues();
        JSONArray evidence = item.getJSONArray("source_refs");
        for (int j = 0; j < evidence.length(); j++) {
          boolean included = false;
          for (int k = 0; k < processed.length(); k++)
            included |= evidence.getString(j).equals(processed.getString(k));
          if (!included) throw new IllegalArgumentException("建议引用了尚未处理的资料");
        }
        v.put("ref", item.getString("review_ref"));
        v.put("title", item.getString("title"));
        v.put("kind", item.getString("kind"));
        v.put("importance", item.getString("importance"));
        v.put("reason", item.getString("reason"));
        v.put("item_json", item.toString());
        v.put("created_at", now);
        v.put("state", "pending");
        db.insertWithOnConflict("ai_reviews", null, v, SQLiteDatabase.CONFLICT_IGNORE);
      }
      for (int i = 0; i < processed.length(); i++) {
        ContentValues done = new ContentValues();
        done.put("state", "done");
        done.putNull("run_id");
        db.update(
            "queued_sources",
            done,
            "ref=? AND run_id=?",
            new String[] {processed.getString(i), id});
      }
      db.execSQL(
          "UPDATE queued_sources SET state='pending',run_id=NULL WHERE run_id=? AND state='leased'",
          new Object[] {id});
      db.execSQL(
          "UPDATE analysis_runs SET state='succeeded',lease_until=0,input_json='[]' WHERE id=?",
          new Object[] {id});
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public void fail(String id, long now, boolean interrupted) {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      db.execSQL(
          "UPDATE queued_sources SET state=CASE WHEN retries>=4 AND ?=0 THEN 'failed' ELSE"
              + " 'pending' END,retries=retries+?,next_attempt=?,run_id=NULL WHERE run_id=? AND"
              + " state='leased'",
          new Object[] {
            interrupted ? 1 : 0, interrupted ? 0 : 1, interrupted ? now : now + 60000L, id
          });
      db.execSQL(
          "UPDATE analysis_runs SET state='failed',lease_until=0,input_json='[]',error_code=? WHERE"
              + " id=? AND state='running'",
          new Object[] {interrupted ? "cancelled" : "analysis_failed", id});
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public void retryFailed() {
    store
        .getWritableDatabase()
        .execSQL(
            "UPDATE queued_sources SET state='pending',retries=0,next_attempt=0 WHERE"
                + " state='failed'");
  }

  public JSONObject queueStats() throws Exception {
    JSONObject out = new JSONObject();
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery("SELECT state,count(*) FROM queued_sources GROUP BY state", null)) {
      while (c.moveToNext()) out.put(c.getString(0), c.getInt(1));
    }
    return out;
  }

  public JSONObject sourceProgress(JSONObject source) throws Exception {
    if (new ConversationPolicy(store).excluded(source))
      return new JSONObject().put("state", "excluded").put("suggestions", 0);
    String base = MemoryStore.reference(source);
    String revision = null, hash = null;
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT revision,content_hash FROM source_snapshots WHERE base_ref=?",
                new String[] {base})) {
      if (c.moveToFirst()) {
        revision = c.getString(0);
        hash = c.getString(1);
      }
    }
    JSONObject result = new JSONObject().put("state", "not_enqueued").put("suggestions", 0);
    if (revision == null) {
      try (Cursor c =
          store
              .getReadableDatabase()
              .rawQuery(
                  "SELECT 1 FROM ai_processed WHERE account_id=? AND msg_id=?",
                  new String[] {
                    source.getString("account_id"), Long.toString(source.getLong("msg_id"))
                  })) {
        if (c.moveToFirst()) return result.put("state", "legacy_done");
      }
      return source.getLong("created_at") < intakeFloor()
          ? result.put("state", "outside_scope")
          : result;
    }
    if (source.optInt("quote_truncated") != 1) {
      String current =
          AnalysisContract.hash(
              source.getInt("type")
                  + "\0"
                  + source.getInt("is_send")
                  + "\0"
                  + source.getLong("created_at")
                  + "\0"
                  + source.optString("content", ""));
      if (!current.equals(hash)) return result.put("state", "changed");
    }
    Set<String> refs = new HashSet<>();
    int total = 0, done = 0, leased = 0, failed = 0;
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT ref,state FROM queued_sources WHERE base_ref=? AND revision=?",
                new String[] {base, revision})) {
      while (c.moveToNext()) {
        refs.add(c.getString(0));
        total++;
        if (c.getString(1).equals("done")) done++;
        if (c.getString(1).equals("leased")) leased++;
        if (c.getString(1).equals("failed")) failed++;
      }
    }
    int suggestions = 0;
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT item_json FROM ai_reviews WHERE instr(item_json,?)>0",
                new String[] {base})) {
      while (c.moveToNext()) {
        JSONArray evidence = new JSONObject(c.getString(0)).optJSONArray("source_refs");
        if (evidence == null) continue;
        for (int i = 0; i < evidence.length(); i++) {
          if (refs.contains(evidence.getString(i))) {
            suggestions++;
            break;
          }
        }
      }
    }
    return result
        .put("suggestions", suggestions)
        .put("total", total)
        .put("done", done)
        .put(
            "state",
            total > 0 && done == total
                ? "done"
                : leased > 0 ? "processing" : failed > 0 ? "failed" : "pending");
  }

  public JSONArray suggestionsForSource(JSONObject source) throws Exception {
    if (new ConversationPolicy(store).excluded(source)) return new JSONArray();
    if (sourceProgress(source).getString("state").equals("changed")) return new JSONArray();
    String base = MemoryStore.reference(source), revision = null;
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT revision FROM source_snapshots WHERE base_ref=?", new String[] {base})) {
      if (c.moveToFirst()) revision = c.getString(0);
    }
    JSONArray result = new JSONArray();
    JSONArray pending = store.suggestions();
    for (int i = 0; i < pending.length(); i++) {
      JSONObject item = pending.getJSONObject(i);
      JSONArray evidence = item.optJSONArray("source_refs");
      if (evidence == null) continue;
      for (int j = 0; j < evidence.length(); j++) {
        String ref = evidence.getString(j);
        if (ref.equals(base) || (revision != null && ref.startsWith(base + ":" + revision + ":"))) {
          result.put(item);
          break;
        }
      }
    }
    return result;
  }

  public JSONArray workItems(boolean includeClosed) throws Exception {
    JSONArray out = new JSONArray();
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT * FROM work_items "
                    + (includeClosed ? "" : "WHERE state NOT IN ('completed','cancelled') ")
                    + "ORDER BY coalesce(starts_at,due_at,9223372036854775807),updated_at DESC"
                    + " LIMIT 200",
                null)) {
      while (c.moveToNext()) out.put(row(c));
    }
    return out;
  }

  public JSONObject work(String id) throws Exception {
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery("SELECT * FROM work_items WHERE id=?", new String[] {id})) {
      return c.moveToFirst() ? row(c) : null;
    }
  }

  public JSONObject reminder(String id) throws Exception {
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery("SELECT * FROM reminders WHERE work_id=?", new String[] {id})) {
      return c.moveToFirst() ? row(c) : null;
    }
  }

  public JSONArray reminders() throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c = store.getReadableDatabase().rawQuery("SELECT * FROM reminders", null)) {
      while (c.moveToNext()) result.put(row(c));
    }
    return result;
  }

  public String confirm(JSONObject proposal, JSONObject edited, Long remindAt, long now)
      throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      String ref = proposal.getString("review_ref");
      String reviewState;
      try (Cursor c =
          db.rawQuery("SELECT state,item_json FROM ai_reviews WHERE ref=?", new String[] {ref})) {
        if (!c.moveToFirst()) throw new IllegalArgumentException("建议不存在");
        reviewState = c.getString(0);
        proposal = new JSONObject(c.getString(1));
      }
      String operation = proposal.optString("operation", "create");
      if (!operation.matches("create|update|complete|cancel"))
        throw new IllegalArgumentException("建议操作无效");
      String id =
          operation.equals("create")
              ? "work:" + AnalysisContract.hash(ref)
              : proposal.getString("target_id");
      if (reviewState.equals("handled") && work(id) != null) return id;
      if (!reviewState.equals("pending")) throw new IllegalStateException("该建议已处理");
      new ConversationPolicy(store).requireProposal(proposal);
      JSONObject source = proposal.getJSONObject("source");
      verifySourceRevisions(source);
      String kind = proposal.getString("kind");
      if (!kind.matches("todo|event")) throw new IllegalArgumentException("该建议不是工作事项");
      JSONObject old = work(id);
      if (!operation.equals("create")) {
        if (old == null || old.getInt("revision") != proposal.getInt("expected_revision")) {
          throw new IllegalStateException("事项已被修改，请重新核对这条建议");
        }
        if (!old.getString("account_id").equals(source.getString("account_id"))
            || !old.getString("talker").equals(source.getString("talker"))) {
          throw new IllegalArgumentException("变更建议的来源不匹配");
        }
        if (old.getString("state").matches("completed|cancelled")) {
          throw new IllegalStateException("事项已经结束，请先手动重新打开");
        }
      } else if (old != null) return id;
      String title = edited.getString("title").trim();
      String state = edited.optString("state", "open");
      String involvement = edited.optString("involvement", "unclear");
      String timezone = edited.getString("timezone");
      java.time.ZoneId.of(timezone);
      if (title.isEmpty()
          || title.length() > 200
          || !state.matches("open|waiting|blocked")
          || !involvement.matches("self|other|unclear"))
        throw new IllegalArgumentException("请核对事项标题和状态");
      Long due = nullable(edited, "due_at"),
          start = nullable(edited, "starts_at"),
          end = nullable(edited, "ends_at");
      TimeRules.validateRange(start, end);
      if (remindAt != null && (remindAt <= now || remindAt - now > 365L * 86400000L)) {
        throw new IllegalArgumentException("提醒时间需在未来一年内");
      }
      int revision = old == null ? 1 : old.getInt("revision") + 1;
      if (operation.equals("complete")) state = "completed";
      if (operation.equals("cancel")) state = "cancelled";
      ContentValues values = new ContentValues();
      values.put("id", id);
      values.put("kind", kind);
      values.put("title", title);
      values.put("state", state);
      values.put("involvement", involvement);
      values.put("location", edited.optString("location", ""));
      values.put("details", edited.optString("details", ""));
      if (edited.optString("location", "").length() > 200
          || edited.optString("details", "").length() > 1200)
        throw new IllegalArgumentException("地点或备注过长");
      values.put("account_id", source.getString("account_id"));
      values.put("talker", source.getString("talker"));
      putNullable(values, "due_at", due);
      putNullable(values, "starts_at", start);
      putNullable(values, "ends_at", end);
      values.put("timezone", timezone);
      values.put("revision", revision);
      values.put("source_json", mergeSources(old, source).toString());
      values.put("created_at", old == null ? now : old.getLong("created_at"));
      values.put("updated_at", now);
      db.insertWithOnConflict("work_items", null, values, SQLiteDatabase.CONFLICT_REPLACE);
      recordChange(db, ref, id, operation, revision, source, now);
      replaceReminder(
          db, id, revision, state.matches("completed|cancelled") ? null : remindAt, now);
      ContentValues handled = new ContentValues();
      handled.put("state", "handled");
      db.update("ai_reviews", handled, "ref=?", new String[] {ref});
      db.setTransactionSuccessful();
      return id;
    } finally {
      db.endTransaction();
    }
  }

  public void confirmMemory(JSONObject proposal, String title, String kind) throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      try (Cursor c =
          db.rawQuery(
              "SELECT state,item_json FROM ai_reviews WHERE ref=?",
              new String[] {proposal.getString("review_ref")})) {
        if (!c.moveToFirst() || !c.getString(0).equals("pending"))
          throw new IllegalStateException("该建议不存在或已处理");
        proposal = new JSONObject(c.getString(1));
      }
      new ConversationPolicy(store).requireProposal(proposal);
      verifySourceRevisions(proposal.getJSONObject("source"));
      store.save(proposal.getJSONObject("source"), title, kind);
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public JSONObject manual(JSONObject source, String kind, String title) throws Exception {
    if (!kind.matches("todo|event")) throw new IllegalArgumentException();
    JSONObject primary = new JSONObject(source.toString());
    String ref =
        "ai:"
            + AnalysisContract.hash(
                "manual\0"
                    + MemoryStore.reference(source)
                    + "\0"
                    + kind
                    + "\0"
                    + UUID.randomUUID());
    primary.remove("_source_ref");
    primary.put("_memory_ref", ref).put("evidence", new JSONArray().put(source));
    JSONObject proposal =
        new JSONObject()
            .put("schema_version", "1.1")
            .put("manual", true)
            .put("review_ref", ref)
            .put("kind", kind)
            .put("title", title)
            .put("operation", "create")
            .put("involvement", "self")
            .put("state", "open")
            .put("importance", "medium")
            .put("reason", "从选中的消息手动建立")
            .put("source", primary)
            .put("timezone", "Asia/Shanghai");
    ContentValues v = new ContentValues();
    v.put("ref", ref);
    v.put("title", title);
    v.put("kind", kind);
    v.put("importance", "medium");
    v.put("reason", "从选中的消息手动建立");
    v.put("item_json", proposal.toString());
    v.put("created_at", System.currentTimeMillis());
    v.put("state", "pending");
    store
        .getWritableDatabase()
        .insertWithOnConflict("ai_reviews", null, v, SQLiteDatabase.CONFLICT_IGNORE);
    return proposal;
  }

  public void changeState(String id, int expectedRevision, String state, long now)
      throws Exception {
    if (!state.matches("open|waiting|blocked|completed|cancelled"))
      throw new IllegalArgumentException();
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      JSONObject item = work(id);
      if (item == null || item.getInt("revision") != expectedRevision)
        throw new IllegalStateException("事项已变化，请刷新");
      int revision = expectedRevision + 1;
      db.execSQL(
          "UPDATE work_items SET state=?,revision=?,updated_at=? WHERE id=?",
          new Object[] {state, revision, now, id});
      JSONObject source =
          new JSONObject().put("source_kind", "user_action").put("at", now).put("state", state);
      recordChange(db, UUID.randomUUID().toString(), id, state, revision, source, now);
      JSONObject existingReminder = reminder(id);
      Long reminderTime =
          existingReminder != null
                  && existingReminder.getString("state").equals("scheduled")
                  && existingReminder.getLong("due_at") > now
                  && !state.matches("completed|cancelled")
              ? existingReminder.getLong("due_at")
              : null;
      replaceReminder(db, id, revision, reminderTime, now);
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public JSONObject editProposal(JSONObject item) throws Exception {
    JSONObject source = new JSONObject(item.getString("source_json"));
    String ref =
        "ai:"
            + AnalysisContract.hash(
                "edit\0" + item.getString("id") + "\0" + item.getInt("revision"));
    source.put("_memory_ref", ref);
    JSONObject proposal =
        new JSONObject()
            .put("review_ref", ref)
            .put("manual", true)
            .put("kind", item.getString("kind"))
            .put("title", item.getString("title"))
            .put("operation", "update")
            .put("target_id", item.getString("id"))
            .put("expected_revision", item.getInt("revision"))
            .put("involvement", item.getString("involvement"))
            .put("state", item.getString("state"))
            .put("timezone", item.getString("timezone"))
            .put("importance", "medium")
            .put("reason", "用户编辑已确认事项")
            .put("source", source);
    ContentValues v = new ContentValues();
    v.put("ref", ref);
    v.put("title", item.getString("title"));
    v.put("kind", item.getString("kind"));
    v.put("importance", "medium");
    v.put("reason", "用户编辑已确认事项");
    v.put("item_json", proposal.toString());
    v.put("created_at", System.currentTimeMillis());
    v.put("state", "pending");
    store
        .getWritableDatabase()
        .insertWithOnConflict("ai_reviews", null, v, SQLiteDatabase.CONFLICT_IGNORE);
    return proposal;
  }

  void verifySourceRevisions(JSONObject source) throws Exception {
    JSONArray evidence = source.optJSONArray("evidence");
    if (evidence == null) evidence = new JSONArray().put(source);
    for (int i = 0; i < evidence.length(); i++) {
      JSONObject part = evidence.getJSONObject(i);
      if (!part.has("_base_ref") || !part.has("_revision")) continue;
      try (Cursor c =
          store
              .getReadableDatabase()
              .rawQuery(
                  "SELECT revision FROM source_snapshots WHERE base_ref=?",
                  new String[] {part.getString("_base_ref")})) {
        if (!c.moveToFirst() || !c.getString(0).equals(part.getString("_revision"))) {
          throw new IllegalStateException("引用的原消息已变化，请重新核对");
        }
      }
    }
  }

  public void requireActiveBatch(JSONObject batch, long now) throws Exception {
    SQLiteDatabase db = store.getReadableDatabase();
    String id = batch.getString("id");
    try (Cursor c =
        db.rawQuery("SELECT state,lease_until FROM analysis_runs WHERE id=?", new String[] {id})) {
      if (!c.moveToFirst() || !c.getString(0).equals("running") || c.getLong(1) <= now)
        throw new IllegalStateException("整理批次已失效，结果未提交");
    }
    ConversationPolicy policy = new ConversationPolicy(store);
    JSONArray sources = batch.getJSONArray("sources");
    for (int i = 0; i < sources.length(); i++) {
      JSONObject source = sources.getJSONObject(i);
      if (policy.excluded(source)) throw new IllegalStateException("该会话已排除 AI 整理");
      try (Cursor c =
          db.rawQuery(
              "SELECT q.state,q.run_id,q.revision,s.revision FROM queued_sources q "
                  + "JOIN source_snapshots s ON s.base_ref=q.base_ref WHERE q.ref=?",
              new String[] {MemoryStore.reference(source)})) {
        if (!c.moveToFirst()
            || !c.getString(0).equals("leased")
            || !id.equals(c.getString(1))
            || !c.getString(2).equals(c.getString(3)))
          throw new IllegalStateException("原消息已变化，整理批次已失效");
      }
    }
  }

  private static JSONObject mergeSources(JSONObject old, JSONObject source) throws Exception {
    JSONObject result = new JSONObject(source.toString());
    JSONArray evidence = result.optJSONArray("evidence");
    if (evidence == null) evidence = new JSONArray().put(source);
    if (old != null) {
      JSONObject previous = new JSONObject(old.getString("source_json"));
      JSONArray earlier = previous.optJSONArray("evidence");
      if (earlier == null) earlier = new JSONArray().put(previous);
      for (int i = 0; i < earlier.length() && evidence.length() < 30; i++)
        evidence.put(earlier.getJSONObject(i));
    }
    return result.put("evidence", evidence);
  }

  private static void recordChange(
      SQLiteDatabase db,
      String ref,
      String id,
      String operation,
      int revision,
      JSONObject source,
      long now) {
    ContentValues v = new ContentValues();
    v.put("id", ref);
    v.put("work_id", id);
    v.put("operation", operation);
    v.put("revision", revision);
    v.put("source_json", source.toString());
    v.put("created_at", now);
    db.insertOrThrow("work_changes", null, v);
  }

  static void replaceReminder(SQLiteDatabase db, String id, int revision, Long due, long now) {
    if (due == null) {
      db.execSQL(
          "UPDATE reminders SET state='cancelled',updated_at=? WHERE work_id=?",
          new Object[] {now, id});
    } else {
      ContentValues v = new ContentValues();
      v.put("work_id", id);
      v.put("revision", revision);
      v.put("due_at", due);
      v.put("state", "scheduled");
      v.put("updated_at", now);
      db.insertWithOnConflict("reminders", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }
  }

  public boolean beginDelivery(String id, int revision, long due, long now, boolean permitted)
      throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      JSONObject item = work(id), rule = reminder(id);
      if (item == null
          || rule == null
          || item.getInt("revision") != revision
          || rule.getInt("revision") != revision
          || rule.getLong("due_at") != due
          || !rule.getString("state").equals("scheduled")
          || item.getString("state").matches("completed|cancelled")) return false;
      if (now < due) return false;
      String state = now - due > 3600000L ? "missed" : permitted ? "posting" : "blocked";
      db.execSQL(
          "UPDATE reminders SET state=?,updated_at=? WHERE work_id=?",
          new Object[] {state, now, id});
      ContentValues v = new ContentValues();
      v.put("id", deliveryId(id, revision, due));
      v.put("work_id", id);
      v.put("revision", revision);
      v.put("due_at", due);
      v.put("state", state);
      v.put("updated_at", now);
      db.insertWithOnConflict("delivery_history", null, v, SQLiteDatabase.CONFLICT_REPLACE);
      db.setTransactionSuccessful();
      return state.equals("posting");
    } finally {
      db.endTransaction();
    }
  }

  public void finishDelivery(String id, int revision, long due, long now) throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      db.execSQL(
          "UPDATE reminders SET state='posted',updated_at=? WHERE work_id=? AND revision=? AND"
              + " due_at=? AND state='posting'",
          new Object[] {now, id, revision, due});
      db.execSQL(
          "UPDATE delivery_history SET state='posted',updated_at=? WHERE id=? AND state='posting'",
          new Object[] {now, deliveryId(id, revision, due)});
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public void uncertainDelivery(String id, int revision, long due, long now) throws Exception {
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      db.execSQL(
          "UPDATE reminders SET state='uncertain',updated_at=? WHERE work_id=? AND revision=? AND"
              + " due_at=? AND state='posting'",
          new Object[] {now, id, revision, due});
      db.execSQL(
          "UPDATE delivery_history SET state='uncertain',updated_at=? WHERE id=? AND"
              + " state='posting'",
          new Object[] {now, deliveryId(id, revision, due)});
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  public boolean acknowledge(String id, int revision, long due, String action, long now)
      throws Exception {
    if (!action.matches("dismiss|snooze|complete")) throw new IllegalArgumentException();
    SQLiteDatabase db = store.getWritableDatabase();
    db.beginTransaction();
    try {
      JSONObject rule = reminder(id), item = work(id);
      if (rule == null
          || item == null
          || rule.getInt("revision") != revision
          || item.getInt("revision") != revision
          || rule.getLong("due_at") != due
          || !rule.getString("state").matches("posting|posted")) return false;
      if (action.equals("snooze") && now - due > 3600000L) return false;
      db.execSQL(
          "UPDATE delivery_history SET state=?,updated_at=? WHERE id=?",
          new Object[] {action, now, deliveryId(id, revision, due)});
      if (action.equals("complete")) changeState(id, revision, "completed", now);
      else if (action.equals("snooze")) replaceReminder(db, id, revision, now + 5L * 60000L, now);
      else
        db.execSQL(
            "UPDATE reminders SET state='dismissed',updated_at=? WHERE work_id=?",
            new Object[] {now, id});
      db.setTransactionSuccessful();
      return true;
    } finally {
      db.endTransaction();
    }
  }

  public static String deliveryId(String id, int revision, long due) throws Exception {
    return AnalysisContract.hash(id + "\0" + revision + "\0" + due);
  }

  public void retryBlocked(long now) {
    store
        .getWritableDatabase()
        .execSQL(
            "UPDATE reminders SET state='scheduled',updated_at=? WHERE state='blocked' AND"
                + " due_at>=?",
            new Object[] {now, now - 3600000L});
  }

  public JSONArray changes(String id) throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c =
        store
            .getReadableDatabase()
            .rawQuery(
                "SELECT operation,revision,source_json,created_at FROM work_changes WHERE work_id=?"
                    + " ORDER BY revision DESC LIMIT 30",
                new String[] {id})) {
      while (c.moveToNext()) result.put(row(c));
    }
    return result;
  }

  static Long nullable(JSONObject item, String key) {
    return item.isNull(key) || !item.has(key) ? null : item.optLong(key);
  }

  private static void putNullable(ContentValues values, String key, Long number) {
    if (number == null) values.putNull(key);
    else values.put(key, number);
  }

  static JSONObject row(Cursor c) throws Exception {
    JSONObject out = new JSONObject();
    for (int i = 0; i < c.getColumnCount(); i++) {
      if (c.isNull(i)) out.put(c.getColumnName(i), JSONObject.NULL);
      else if (c.getType(i) == Cursor.FIELD_TYPE_INTEGER) out.put(c.getColumnName(i), c.getLong(i));
      else out.put(c.getColumnName(i), c.getString(i));
    }
    return out;
  }
}
