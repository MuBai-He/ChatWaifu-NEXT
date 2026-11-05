package com.mubai.infoinbox;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.json.JSONArray;
import org.json.JSONObject;

/** Confirmed memory snapshots live in app-private storage, separately from the raw archive. */
public final class MemoryStore implements AutoCloseable {
  // Leases share Android's WAL connection pool and writer scheduling. Each owner
  // closes only its lease; an Activity must not close a live background owner's DB.
  private static final java.util.Map<String, SharedDatabase> OPEN = new java.util.HashMap<>();
  private final SharedDatabase shared;
  private final String path;
  private boolean closed;
  private final Context application;
  private final boolean mainStore;

  private static final class SharedDatabase extends SQLiteOpenHelper {
    int owners;

    SharedDatabase(Context context, String name) {
      super(context, name, null, 9);
      setWriteAheadLoggingEnabled(true);
    }

    @Override public void onCreate(SQLiteDatabase db) { createSchema(db); }
    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
      upgradeSchema(db, oldVersion);
    }
  }

  public MemoryStore(Context context) {
    this(context, "information.db");
  }

  Context applicationContext() { return application; }

  MemoryStore(Context context, String name) {
    application = context.getApplicationContext();
    mainStore = name.equals("information.db");
    path = context.getDatabasePath(name).getAbsoluteFile().toPath().normalize().toString();
    synchronized (OPEN) {
      SharedDatabase existing = OPEN.get(path);
      if (existing == null) {
        Context storage = context.getDatabasePath(name).equals(application.getDatabasePath(name))
            ? application : context;
        existing = new SharedDatabase(storage, name);
        OPEN.put(path, existing);
      }
      shared = existing;
      shared.owners++;
    }
  }

  public synchronized SQLiteDatabase getWritableDatabase() {
    if (closed) throw new IllegalStateException("资料库已关闭");
    return shared.getWritableDatabase();
  }

  public synchronized SQLiteDatabase getReadableDatabase() {
    if (closed) throw new IllegalStateException("资料库已关闭");
    return shared.getReadableDatabase();
  }

  @Override public synchronized void close() {
    if (closed) return;
    closed = true;
    synchronized (OPEN) {
      if (--shared.owners == 0) {
        try { shared.close(); } finally { OPEN.remove(path); }
      }
    }
  }

  void scheduleKnowledge() {
    // Nested domain saves defer notification to the outer commit. No Binder,
    // foreground-service start or scheduler operation may extend that write lock.
    if (getWritableDatabase().inTransaction()) return;
    if (mainStore && !RuntimeMessageService.requestSync(application))
      KnowledgeJob.enqueueNow(application);
  }

  private static void createSchema(SQLiteDatabase db) {
    db.execSQL(
        "CREATE TABLE memories(ref TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,quote"
            + " TEXT NOT NULL,source_json TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at"
            + " INTEGER NOT NULL)");
    db.execSQL("CREATE TABLE source_states(ref TEXT PRIMARY KEY,state TEXT NOT NULL)");
    createAiTables(db);
    OrganizerStore.createTables(db);
    ConversationPolicy.createTable(db);
    KnowledgeStore.createTables(db);
    RuntimeProcessing.createTables(db);
    ensureReadIndexes(db);
  }

  private static void upgradeSchema(SQLiteDatabase db, int oldVersion) {
    if (oldVersion < 2) createAiTables(db);
    if (oldVersion < 3) OrganizerStore.createTables(db);
    if (oldVersion < 4) ConversationPolicy.createTable(db);
    if (oldVersion < 5) KnowledgeStore.createTables(db);
    if (oldVersion < 6) RuntimeProcessing.createTables(db);
    if (oldVersion < 7) OrganizerStore.ensureQueuePriority(db);
    if (oldVersion < 8) OrganizerStore.ensureQueueScopeIndex(db);
    if (oldVersion < 9) ensureReadIndexes(db);
  }

  private static void ensureReadIndexes(SQLiteDatabase db) {
    // Policy reads have no server-space filter: the composite (space,id) key cannot serve them.
    db.execSQL("CREATE INDEX IF NOT EXISTS knowledge_shadow_source ON knowledge_shadow(id)");
    db.execSQL("CREATE INDEX IF NOT EXISTS knowledge_outbox_source ON knowledge_outbox(id,action)");
  }

  private static void createAiTables(SQLiteDatabase db) {
    db.execSQL(
        "CREATE TABLE ai_processed(account_id TEXT NOT NULL,msg_id INTEGER NOT NULL,PRIMARY"
            + " KEY(account_id,msg_id))");
    db.execSQL(
        "CREATE TABLE ai_reviews(ref TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT"
            + " NULL,importance TEXT NOT NULL,reason TEXT NOT NULL,item_json TEXT NOT"
            + " NULL,created_at INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending')");
  }

  public static String reference(JSONObject source) throws Exception {
    String item = source.optString("_memory_ref", "");
    if (item.matches("ai:[0-9a-f]{64}")) return item;
    if (!source.optString("_knowledge_id").isEmpty())
      return "remote:" + AnalysisContract.hash(source.getString("_knowledge_id"));
    String segment = source.optString("_source_ref", "");
    if (segment.matches("wechat:[0-9a-f]{64}:[0-9a-f]{64}:[0-9]+")) return segment;
    String identity =
        "wechat\0"
            + source.getString("account_id")
            + "\0"
            + source.getString("talker")
            + "\0"
            + source.getLong("msg_id");
    byte[] digest =
        MessageDigest.getInstance("SHA-256").digest(identity.getBytes(StandardCharsets.UTF_8));
    StringBuilder result = new StringBuilder("wechat:");
    for (byte b : digest) result.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
    return result.toString();
  }

  public String state(JSONObject source) throws Exception {
    try (Cursor c =
        getReadableDatabase()
            .query(
                "source_states",
                new String[] {"state"},
                "ref=?",
                new String[] {reference(source)},
                null,
                null,
                null)) {
      return c.moveToFirst() ? c.getString(0) : "pending";
    }
  }

  public void mark(JSONObject source, String state) throws Exception {
    if (!state.equals("handled") && !state.equals("ignored") && !state.equals("pending"))
      throw new IllegalArgumentException();
    ContentValues values = new ContentValues();
    values.put("ref", reference(source));
    values.put("state", state);
    getWritableDatabase()
        .insertWithOnConflict("source_states", null, values, SQLiteDatabase.CONFLICT_REPLACE);
  }

  public void save(JSONObject source, String title, String kind) throws Exception {
    new OrganizerStore(this).verifySourceRevisions(source);
    if (title.trim().isEmpty() || title.length() > 200)
      throw new IllegalArgumentException("请输入 1–200 字的记忆标题");
    String ref = reference(source);
    long now = System.currentTimeMillis();
    SQLiteDatabase db = getWritableDatabase();
    db.beginTransactionNonExclusive();
    try {
      long created = now;
      try (Cursor c =
          db.rawQuery("SELECT created_at FROM memories WHERE ref=?", new String[] {ref})) {
        if (c.moveToFirst()) created = c.getLong(0);
      }
      ContentValues v = new ContentValues();
      v.put("ref", ref);
      v.put("title", title.trim());
      v.put("kind", kind);
      v.put("quote", source.optString("_information_summary", source.optString("content", "")));
      v.put("source_json", source.toString());
      v.put("created_at", created);
      v.put("updated_at", now);
      db.insertWithOnConflict("memories", null, v, SQLiteDatabase.CONFLICT_REPLACE);
      ContentValues s = new ContentValues();
      s.put("ref", ref);
      s.put("state", "handled");
      db.insertWithOnConflict("source_states", null, s, SQLiteDatabase.CONFLICT_REPLACE);
      ContentValues reviewed = new ContentValues();
      reviewed.put("state", "handled");
      db.update("ai_reviews", reviewed, "ref=?", new String[] {ref});
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
    scheduleKnowledge();
  }

  public JSONArray list() throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c =
        getReadableDatabase()
            .rawQuery(
                "SELECT ref,title,kind,quote,source_json,created_at,updated_at FROM memories ORDER"
                    + " BY updated_at DESC",
                null)) {
      while (c.moveToNext()) {
        result.put(memoryRow(c));
      }
    }
    return result;
  }

  private static JSONObject memoryRow(Cursor c) throws Exception {
    JSONObject item = new JSONObject();
    item.put("ref", c.getString(0));
    item.put("title", c.getString(1));
    item.put("kind", c.getString(2));
    item.put("quote", c.getString(3));
    JSONObject source = new JSONObject(c.getString(4));
    if (source.has("_knowledge_id")) source.put("_information_summary", c.getString(3));
    item.put("source", source);
    item.put(
        "summary",
        source.has("_information_summary") || !source.has("content") ? c.getString(3) : "");
    item.put("verification_state", "user_confirmed");
    item.put("created_at", c.getLong(5));
    item.put("updated_at", c.getLong(6));
    return item;
  }

  public JSONObject memoryPage(String search, String kind, int page, int size) throws Exception {
    size = Math.max(1, Math.min(50, size));
    String where = "1=1";
    java.util.List<String> args = new java.util.ArrayList<>();
    if (!kind.equals("全部")) { where += " AND kind=?"; args.add(kind); }
    String query = search.trim();
    if (!query.isEmpty()) {
      where += " AND (instr(lower(title),?)>0 OR instr(lower(quote),?)>0 OR instr(lower(source_json),?)>0)";
      String needle = query.toLowerCase(java.util.Locale.ROOT);
      for (int i = 0; i < 3; i++) args.add(needle);
    }
    String[] bindings = args.toArray(new String[0]);
    SQLiteDatabase db = getReadableDatabase();
    int total;
    try (Cursor c = db.rawQuery("SELECT count(*) FROM memories WHERE " + where, bindings)) {
      c.moveToFirst(); total = c.getInt(0);
    }
    page = Math.max(0, Math.min(page, Math.max(0, (total - 1) / size)));
    JSONArray items = new JSONArray();
    try (Cursor c = db.rawQuery(
        "SELECT ref,title,kind,quote,source_json,created_at,updated_at FROM memories WHERE "
            + where + " ORDER BY updated_at DESC,ref LIMIT " + size + " OFFSET " + (page * size), bindings)) {
      while (c.moveToNext()) items.put(memoryRow(c));
    }
    return new JSONObject().put("items", items).put("total", total).put("page", page);
  }

  public void remove(String ref) {
    SQLiteDatabase db = getWritableDatabase();
    db.beginTransactionNonExclusive();
    try {
      db.delete("memories", "ref=?", new String[] {ref});
      db.delete("source_states", "ref=?", new String[] {ref});
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
    scheduleKnowledge();
  }

  public JSONObject exportDocument() throws Exception {
    return new JSONObject()
        .put("schema_version", "1.0")
        .put("document_type", "confirmed_information_memories")
        .put("source_kind", "wechat_root")
        .put("exported_at", System.currentTimeMillis())
        .put("items", list());
  }

  public JSONArray suggestions() throws Exception {
    return suggestions(System.currentTimeMillis());
  }

  public JSONArray suggestions(long now) throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c =
        getReadableDatabase()
            .rawQuery(
                "SELECT item_json FROM ai_reviews WHERE state='pending' ORDER BY CASE importance"
                    + " WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,created_at DESC",
                null)) {
      ConversationPolicy policy = new ConversationPolicy(this, true);
      while (c.moveToNext()) {
        JSONObject item = new JSONObject(c.getString(0));
        ProposalRelevance.annotate(item, now);
        if (policy.allowsProposal(item)) result.put(item);
      }
    }
    return result;
  }

  public void reviewState(String ref, String state) {
    if (!state.equals("handled") && !state.equals("ignored")) throw new IllegalArgumentException();
    ContentValues v = new ContentValues();
    v.put("state", state);
    getWritableDatabase().update("ai_reviews", v, "ref=?", new String[] {ref});
  }

  public void acceptAnalysis(JSONObject response, JSONArray input) throws Exception {
    java.util.Map<String, JSONObject> sources = new java.util.HashMap<>();
    for (int i = 0; i < input.length(); i++)
      sources.put(reference(input.getJSONObject(i)), input.getJSONObject(i));
    SQLiteDatabase db = getWritableDatabase();
    db.beginTransactionNonExclusive();
    try {
      JSONArray items = response.getJSONArray("items");
      for (int i = 0; i < items.length(); i++) {
        JSONObject item = items.getJSONObject(i);
        new ConversationPolicy(this).requireProposal(item);
        String ref = reference(item.getJSONObject("source"));
        item.put("review_ref", ref);
        ContentValues v = new ContentValues();
        v.put("ref", ref);
        v.put("title", item.getString("title"));
        v.put("kind", item.getString("kind"));
        v.put("importance", item.getString("importance"));
        v.put("reason", item.getString("reason"));
        v.put("item_json", item.toString());
        v.put("created_at", System.currentTimeMillis());
        v.put("state", "pending");
        db.insertWithOnConflict("ai_reviews", null, v, SQLiteDatabase.CONFLICT_IGNORE);
      }
      JSONArray refs = response.getJSONArray("processed_refs");
      for (int i = 0; i < refs.length(); i++) {
        JSONObject source = sources.get(refs.getString(i));
        if (source == null) throw new Exception("分析批次来源不一致");
        ContentValues v = new ContentValues();
        v.put("account_id", source.getString("account_id"));
        v.put("msg_id", source.getLong("msg_id"));
        db.insertWithOnConflict("ai_processed", null, v, SQLiteDatabase.CONFLICT_IGNORE);
      }
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }
}
