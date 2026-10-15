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
public final class MemoryStore extends SQLiteOpenHelper {
  private final Context application;
  private final boolean mainStore;

  public MemoryStore(Context context) {
    this(context, "information.db");
  }

  MemoryStore(Context context, String name) {
    super(context, name, null, 5);
    application = context.getApplicationContext();
    mainStore = name.equals("information.db");
    setWriteAheadLoggingEnabled(true);
  }

  void scheduleKnowledge() {
    if (mainStore) KnowledgeJob.enqueueNow(application);
  }

  @Override
  public void onCreate(SQLiteDatabase db) {
    db.execSQL(
        "CREATE TABLE memories(ref TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,quote"
            + " TEXT NOT NULL,source_json TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at"
            + " INTEGER NOT NULL)");
    db.execSQL("CREATE TABLE source_states(ref TEXT PRIMARY KEY,state TEXT NOT NULL)");
    createAiTables(db);
    OrganizerStore.createTables(db);
    ConversationPolicy.createTable(db);
    KnowledgeStore.createTables(db);
  }

  @Override
  public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
    if (oldVersion < 2) createAiTables(db);
    if (oldVersion < 3) OrganizerStore.createTables(db);
    if (oldVersion < 4) ConversationPolicy.createTable(db);
    if (oldVersion < 5) KnowledgeStore.createTables(db);
  }

  private void createAiTables(SQLiteDatabase db) {
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
    db.beginTransaction();
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
      v.put("quote", source.optString("content", ""));
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
        JSONObject item = new JSONObject();
        item.put("ref", c.getString(0));
        item.put("title", c.getString(1));
        item.put("kind", c.getString(2));
        item.put("quote", c.getString(3));
        item.put("source", new JSONObject(c.getString(4)));
        item.put("verification_state", "user_confirmed");
        item.put("created_at", c.getLong(5));
        item.put("updated_at", c.getLong(6));
        result.put(item);
      }
    }
    return result;
  }

  public void remove(String ref) {
    SQLiteDatabase db = getWritableDatabase();
    db.beginTransaction();
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
    JSONArray result = new JSONArray();
    try (Cursor c =
        getReadableDatabase()
            .rawQuery(
                "SELECT item_json FROM ai_reviews WHERE state='pending' ORDER BY CASE importance"
                    + " WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,created_at DESC",
                null)) {
      ConversationPolicy policy = new ConversationPolicy(this);
      while (c.moveToNext()) {
        JSONObject item = new JSONObject(c.getString(0));
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
    db.beginTransaction();
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
