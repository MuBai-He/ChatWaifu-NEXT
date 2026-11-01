package com.mubai.infoinbox;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Canonical revision shadows and immutable retries share the local organizer transaction. */
public final class KnowledgeStore {
  final MemoryStore memory;
  final SQLiteDatabase db;
  final String space;

  KnowledgeStore(MemoryStore memory, String base) throws Exception {
    this.memory = memory;
    this.db = memory.getWritableDatabase();
    this.space = AnalysisContract.hash(base);
  }

  static void createTables(SQLiteDatabase db) {
    db.execSQL(
        "CREATE TABLE knowledge_meta(space TEXT,key TEXT,value TEXT,PRIMARY KEY(space,key))");
    db.execSQL(
        "CREATE TABLE knowledge_shadow(space TEXT,id TEXT,revision INTEGER,hash TEXT,payload"
            + " TEXT,local_id TEXT,conflict_id TEXT,PRIMARY KEY(space,id))");
    db.execSQL(
        "CREATE TABLE knowledge_outbox(space TEXT,id TEXT,operation TEXT,expected_revision"
            + " INTEGER,action TEXT,payload TEXT,hash TEXT,kind TEXT,PRIMARY KEY(space,id))");
    db.execSQL(
        "CREATE TABLE knowledge_sources(space TEXT,account_id TEXT,msg_id INTEGER,content"
            + " TEXT,source_json TEXT,ids TEXT,PRIMARY KEY(space,account_id,msg_id))");
    db.execSQL(
        "CREATE TABLE knowledge_rooms(space TEXT,room_id TEXT,account_id TEXT,talker TEXT,label"
            + " TEXT,revision INTEGER DEFAULT 0,remote_excluded INTEGER DEFAULT 0,desired"
            + " INTEGER,operation TEXT,PRIMARY KEY(space,room_id))");
    db.execSQL(
        "CREATE TABLE knowledge_source_refs(space TEXT,id TEXT,account_id TEXT,msg_id"
            + " INTEGER,PRIMARY KEY(space,id))");
    db.execSQL(
        "CREATE TABLE knowledge_priority(space TEXT,account_id TEXT,msg_id INTEGER,PRIMARY"
            + " KEY(space,account_id,msg_id))");
    db.execSQL(
        "CREATE TABLE knowledge_ack_outbox(space TEXT,id TEXT,revision INTEGER,PRIMARY"
            + " KEY(space,id))");
  }

  static void policyChanged(SQLiteDatabase db, JSONObject room, boolean excluded) throws Exception {
    db.execSQL(
        "UPDATE knowledge_rooms SET desired=?,operation=? WHERE account_id=? AND talker=?",
        new Object[] {
          excluded ? 1 : 0,
          UUID.randomUUID().toString(),
          room.getString("account_id"),
          room.getString("talker")
        });
  }

  String meta(String key, String fallback) {
    try (Cursor c =
        db.rawQuery(
            "SELECT value FROM knowledge_meta WHERE space=? AND key=?",
            new String[] {space, key})) {
      return c.moveToFirst() ? c.getString(0) : fallback;
    }
  }

  void meta(String key, long value) {
    putMeta(key, Long.toString(value));
  }

  void putMeta(String key, String value) {
    db.execSQL(
        "INSERT OR REPLACE INTO knowledge_meta VALUES(?,?,?)", new Object[] {space, key, value});
  }

  String device() {
    String id = meta("device", "");
    if (id.isEmpty()) {
      id = UUID.randomUUID().toString();
      putMeta("device", id);
    }
    return id;
  }

  long epoch() {
    return Long.parseLong(meta("epoch", "0"));
  }

  JSONObject shadow(String id) throws Exception {
    try (Cursor c =
        db.rawQuery(
            "SELECT * FROM knowledge_shadow WHERE space=? AND id=?", new String[] {space, id})) {
      return c.moveToFirst() ? row(c) : null;
    }
  }

  static JSONObject row(Cursor c) throws Exception {
    JSONObject out = new JSONObject();
    for (int i = 0; i < c.getColumnCount(); i++)
      out.put(
          c.getColumnName(i),
          c.isNull(i)
              ? JSONObject.NULL
              : c.getType(i) == Cursor.FIELD_TYPE_INTEGER ? c.getLong(i) : c.getString(i));
    return out;
  }

  JSONObject pending(String id) throws Exception {
    try (Cursor c =
        db.rawQuery(
            "SELECT * FROM knowledge_outbox WHERE space=? AND id=?", new String[] {space, id})) {
      return c.moveToFirst() ? row(c) : null;
    }
  }

  String registerRoom(JSONObject source) throws Exception {
    String room = KnowledgeWire.room(source);
    String account = source.getString("account_id"), talker = source.getString("talker");
    String label = MessagePresentation.conversationTitle(source);
    try (Cursor known = db.rawQuery(
        "SELECT account_id,talker,label FROM knowledge_rooms WHERE space=? AND room_id=?",
        new String[] {space, room})) {
      if (known.moveToFirst() && account.equals(known.getString(0))
          && talker.equals(known.getString(1)) && label.equals(known.getString(2))) return room;
    }
    boolean excluded = new ConversationPolicy(memory).excluded(source);
    db.execSQL(
        "INSERT OR IGNORE INTO"
            + " knowledge_rooms(space,room_id,account_id,talker,label,desired,operation)"
            + " VALUES(?,?,?,?,?,?,?)",
        new Object[] {
          space,
          room,
          account,
          talker,
          label,
          excluded ? 1 : null,
          excluded ? UUID.randomUUID().toString() : null
        });
    db.execSQL(
        "UPDATE knowledge_rooms SET account_id=?,talker=?,label=? WHERE space=? AND room_id=?"
            + " AND (account_id<>? OR talker<>? OR label<>?)",
        new Object[] {
          account,
          talker,
          label,
          space,
          room,
          account,
          talker,
          label
        });
    return room;
  }

  void captureLocalState() throws Exception {
    db.beginTransactionNonExclusive();
    try {
      JSONArray exclusions = new ConversationPolicy(memory).list();
      String fingerprint = AnalysisContract.hash(exclusions.toString());
      if (!fingerprint.equals(meta("local_policy_fingerprint", ""))) {
        for (int i = 0; i < exclusions.length(); i++) {
          if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
          registerRoom(exclusions.getJSONObject(i));
        }
        putMeta("local_policy_fingerprint", fingerprint);
      }
      captureConfirmed();
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  boolean excluded(String room) {
    try (Cursor c =
        db.rawQuery(
            "SELECT k.remote_excluded,k.desired,EXISTS(SELECT 1 FROM conversation_exclusions e"
                + " WHERE e.account_id=k.account_id AND e.talker=k.talker) FROM knowledge_rooms k"
                + " WHERE k.space=? AND k.room_id=?",
            new String[] {space, room})) {
      return c.moveToFirst()
          && (c.getInt(2) == 1 || c.getInt(0) == 1 || (!c.isNull(1) && c.getInt(1) == 1));
    }
  }

  void queue(JSONObject draft, String localId, String action) throws Exception {
    String id = draft.getString("record_id"), hash = KnowledgeWire.fingerprint(draft);
    JSONObject old = shadow(id);
    if (pending(id) != null || (old != null && !old.isNull("conflict_id"))) return;
    if (old != null && old.getString("hash").equals(hash) && action.equals("upsert")) return;
    if (old != null && new JSONObject(old.getString("payload")).optBoolean("deleted")) return;
    if(old!=null && action.equals("upsert")){
      JSONObject previous=new JSONObject(old.getString("payload")),origin=previous.getJSONObject("origin");
      if(origin.optBoolean("external_bound")&& !origin.optString("work_item_id").isEmpty()){
        boolean changed=false;for(String key:new String[]{"title","text","state","due_at","due_date","starts_at","ends_at"})if(!KnowledgeWire.canonical(previous.opt(key)).equals(KnowledgeWire.canonical(draft.opt(key))))changed=true;
        if(changed){
          JSONObject changes=new JSONObject().put("title",draft.getString("title")).put("details",draft.getString("text"));
          for(String key:new String[]{"due_at","starts_at","ends_at","remind_at"})changes.put(key,draft.isNull(key)?JSONObject.NULL:java.time.Instant.ofEpochMilli(draft.getLong(key)).toString());
          if(draft.has("due_date"))changes.put("due_date",draft.opt("due_date"));
          String state=draft.optString("state"),command=state.equals("completed")?"complete":state.equals("cancelled")?"cancel":"edit";
          RuntimeActions.enqueue(this,"/v1/personal-assistant/work-items/"+android.net.Uri.encode(origin.getString("work_item_id")),new JSONObject().put("expected_revision",origin.getInt("work_revision")).put("action",command).put("changes",changes),draft.getString("title"));
          db.execSQL("UPDATE knowledge_shadow SET hash=? WHERE space=? AND id=?",new Object[]{hash,space,id});
          return;
        }
      }
    }

    if (old == null)
      db.execSQL(
          "INSERT INTO knowledge_shadow VALUES(?,?,0,'','{}',?,NULL)",
          new Object[] {space, id, localId});
    db.execSQL(
        "INSERT INTO knowledge_outbox VALUES(?,?,?,?,?,?,?,?)",
        new Object[] {
          space,
          id,
          UUID.randomUUID().toString(),
          old == null ? 0 : old.getLong("revision"),
          action,
          draft.toString(),
          hash,
          draft.getString("kind")
        });
  }

  void intake(JSONArray sources) throws Exception {
    db.beginTransactionNonExclusive();
    try {
      for (int i = 0; i < sources.length(); i++) {
        JSONObject source = sources.getJSONObject(i);
        String room = registerRoom(source);
        if (excluded(room)) continue;
        String captureKey="capture_started:"+room;
        long captured=Long.parseLong(meta(captureKey,"0"));
        if(captured==0){captured=System.currentTimeMillis();putMeta(captureKey,Long.toString(captured));}
        long messageAt=source.getLong("created_at");
        String mode=source.optString("_capture_mode","historical");
        long sourceCaptured=source.optLong("_captured_at",captured);
        JSONArray drafts = KnowledgeWire.sourceRecords(source), ids = new JSONArray();
        boolean deferred = false;
        for (int j = 0; j < drafts.length(); j++) {
          JSONObject draft = drafts.getJSONObject(j);
          draft.getJSONObject("origin").put("capture_mode",mode);
          draft.getJSONObject("origin").put("captured_at",sourceCaptured);
          JSONObject flight = pending(draft.getString("record_id"));
          if (flight != null && !flight.getString("hash").equals(KnowledgeWire.fingerprint(draft)))
            deferred = true;
          queue(draft, "", "upsert");
          ids.put(draft.getString("record_id"));
          db.execSQL(
              "INSERT OR REPLACE INTO knowledge_source_refs VALUES(?,?,?,?)",
              new Object[] {
                space,
                draft.getString("record_id"),
                source.getString("account_id"),
                source.getLong("msg_id")
              });
        }
        try (Cursor c =
            db.rawQuery(
                "SELECT ids FROM knowledge_sources WHERE space=? AND account_id=? AND msg_id=?",
                new String[] {space, source.getString("account_id"), source.getString("msg_id")})) {
          if (c.moveToFirst()) {
            JSONArray prior = new JSONArray(c.getString(0));
            for (int j = drafts.length(); j < prior.length(); j++) {
              JSONObject previous = shadow(prior.getString(j));
              if (previous != null && previous.getLong("revision") > 0)
                queue(strip(new JSONObject(previous.getString("payload"))), "", "forget");
            }
          }
        }
        // Keep a trusted native locator; web source references never become Root commands.
        db.execSQL(
            "INSERT OR REPLACE INTO knowledge_sources VALUES(?,?,?,?,?,?)",
            new Object[] {
              space,
              source.getString("account_id"),
              source.getLong("msg_id"),
              deferred ? "" : source.getString("content"),
              source.toString(),
              ids.toString()
            });
      }
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  static JSONObject strip(JSONObject record) throws Exception {
    JSONObject draft = new JSONObject(record.toString());
    draft.remove("revision");
    draft.remove("deleted");
    draft.remove("updated_at");
    draft.remove("sync");
    return draft;
  }

  JSONArray sourceRefs(JSONObject source) throws Exception {
    JSONArray refs = new JSONArray(), evidence = source.optJSONArray("evidence");
    if (evidence == null) evidence = new JSONArray().put(source);
    for (int i = 0; i < evidence.length() && refs.length() < 30; i++) {
      JSONObject item = evidence.getJSONObject(i);
      if (!item.has("account_id") || !item.has("msg_id")) continue;
      try (Cursor c =
          db.rawQuery(
              "SELECT ids FROM knowledge_sources WHERE space=? AND account_id=? AND msg_id=?",
              new String[] {space, item.getString("account_id"), item.getString("msg_id")})) {
        if (!c.moveToFirst()) continue;
        JSONArray ids = new JSONArray(c.getString(0));
        for (int j = 0; j < ids.length() && refs.length() < 30; j++) {
          JSONObject old = shadow(ids.getString(j));
          if (old != null
              && old.getLong("revision") > 0
              && !new JSONObject(old.getString("payload")).optBoolean("deleted"))
            refs.put(
                new JSONObject()
                    .put("record_id", ids.getString(j))
                    .put("revision", old.getLong("revision")));
        }
      }
    }
    return refs;
  }

  private JSONObject template(String local, JSONObject fallback) throws Exception {
    try (Cursor c =
        db.rawQuery(
            "SELECT payload FROM knowledge_shadow WHERE space=? AND local_id=? AND revision>0 LIMIT"
                + " 1",
            new String[] {space, local})) {
      if (c.moveToFirst()) {
        JSONObject old = strip(new JSONObject(c.getString(0)));
        for (String key : new String[] {"record_id", "room_id", "origin", "occurred_at"})
          fallback.put(key, old.get(key));
      }
    }
    return fallback;
  }

  private JSONArray preservedRefs(String local, JSONObject source) throws Exception {
    JSONArray needed = source.optJSONArray("evidence");
    if (needed == null) needed = new JSONArray().put(source);
    for (int i = 0; i < needed.length(); i++) {
      JSONObject item = needed.getJSONObject(i);
      if (item.has("account_id") && item.has("msg_id"))
        db.execSQL(
            "INSERT OR IGNORE INTO knowledge_priority VALUES(?,?,?)",
            new Object[] {space, item.getString("account_id"), item.getLong("msg_id")});
    }
    JSONArray previous = new JSONArray();
    boolean hasPrevious = false;
    try (Cursor c =
        db.rawQuery(
            "SELECT payload FROM knowledge_shadow WHERE space=? AND local_id=? AND revision>0 LIMIT"
                + " 1",
            new String[] {space, local})) {
      if (c.moveToFirst()) {
        hasPrevious = true;
        previous = new JSONObject(c.getString(0)).getJSONArray("source_refs");
      }
    }
    if (!source.optString("_analysis_context_version").equals("1.2"))
      return hasPrevious ? previous : sourceRefs(source);
    // Hold the projection until every piece of the reviewed dialogue has a canonical source.
    for (int i = 0; i < needed.length(); i++) {
      JSONObject part = needed.getJSONObject(i);
      if (!part.has("account_id") || !part.has("msg_id")) continue;
      try (Cursor c =
          db.rawQuery(
              "SELECT ids FROM knowledge_sources WHERE space=? AND account_id=? AND msg_id=?",
              new String[] {space, part.getString("account_id"), part.getString("msg_id")})) {
        if (!c.moveToFirst()) return null;
        JSONArray ids = new JSONArray(c.getString(0));
        for (int j = 0; j < ids.length(); j++) {
          JSONObject known = shadow(ids.getString(j));
          if (known == null
              || known.getLong("revision") <= 0
              || new JSONObject(known.getString("payload")).optBoolean("deleted")) return null;
        }
      }
    }
    JSONArray current = sourceRefs(source), result = new JSONArray();
    java.util.Set<String> seen = new java.util.HashSet<>();
    for (JSONArray refs : new JSONArray[] {current, previous})
      for (int i = 0; i < refs.length() && result.length() < 30; i++) {
        JSONObject ref = refs.getJSONObject(i);
        if (seen.add(ref.getString("record_id"))) result.put(ref);
      }
    return result;
  }

  JSONObject localRecord(String id, String kind) throws Exception {
    if (kind.equals("memory")) {
      try (Cursor c = db.rawQuery("SELECT * FROM memories WHERE ref=?", new String[] {id})) {
        if (!c.moveToFirst()) return null;
        JSONObject item = row(c), source = new JSONObject(item.getString("source_json"));
        String room = source.has("account_id") ? registerRoom(source) : "manual";
        JSONArray refs = preservedRefs(id, source);
        if (refs == null) return null;
        return template(
                id,
                KnowledgeWire.draft(
                    "memory:" + AnalysisContract.hash(id),
                    "memory",
                    room,
                    item.getLong("created_at")))
            .put("title", item.getString("title"))
            .put("text", item.getString("quote"))
            .put("confirmed", true)
            .put("source_refs", refs);
      }
    }
    JSONObject item = new OrganizerStore(memory).work(id);
    if (item == null) return null;
    JSONObject source = new JSONObject(item.getString("source_json"));
    String room = registerRoom(item);
    JSONArray refs = preservedRefs(id, source);
    if (refs == null) return null;
    JSONObject out =
        template(
                id,
                KnowledgeWire.draft(id, item.getString("kind"), room, item.getLong("created_at")))
            .put("title", item.getString("title"))
            .put("text", item.getString("details"))
            .put("state", item.getString("state"))
            .put("confirmed", true)
            .put("timezone", item.getString("timezone"))
            .put("source_refs", refs);
    out.getJSONObject("origin").put("involvement",item.optString("involvement","unclear")).put("native_work_revision",item.getInt("revision"));
    for (String key : new String[] {"due_at", "starts_at", "ends_at"}) out.put(key, item.opt(key));
    if(!item.isNull("due_date") || Integer.parseInt(meta("processing_epoch","0"))>0)out.put("due_date",item.opt("due_date"));
    JSONObject reminder = new OrganizerStore(memory).reminder(id);
    if (reminder != null && !reminder.getString("state").matches("cancelled|dismissed"))
      out.put("remind_at", reminder.getLong("due_at"));
    JSONObject occurrence=out.getJSONObject("origin").optJSONObject("reminder_occurrence");
    if(occurrence!=null) {
      boolean primary=device().equals(occurrence.optString("primary_device_id"));
      if(!primary && (reminder==null || !reminder.optString("state").equals("scheduled"))) {
        String occurrenceState=occurrence.optString("state");
        out.put("remind_at",occurrenceState.matches("scheduled|accepted|presented|blocked|outcome_unknown") && !occurrence.isNull("scheduled_at") ? java.time.Instant.parse(occurrence.getString("scheduled_at")).toEpochMilli() : JSONObject.NULL);
      } else if(reminder!=null) {
        String localState=reminder.optString("state");
        String state=localState.equals("posted")?"presented":localState.equals("posting")||localState.equals("uncertain")?"outcome_unknown":localState;
        if(state.equals("scheduled") && !occurrence.isNull("scheduled_at") && reminder.getLong("due_at")!=java.time.Instant.parse(occurrence.getString("scheduled_at")).toEpochMilli())state="snoozed";
        out.getJSONObject("origin").put("device_reminder",new JSONObject().put("occurrence_id",occurrence.getString("occurrence_id")).put("state",state).put("observed_at",reminder.getLong("updated_at")));
        if(!primary&&localState.equals("scheduled"))out.getJSONObject("origin").put("requested_primary_device",device());
      }
    }
    return out;
  }

  void captureConfirmed() throws Exception {
    db.beginTransactionNonExclusive();
    try {
      for (String table : new String[] {"memories", "work_items"}) {
        try (Cursor c =
            db.rawQuery(
                "SELECT "
                    + (table.equals("memories") ? "ref,'memory'" : "id,kind")
                    + " FROM "
                    + table,
                null)) {
          while (c.moveToNext()) {
            JSONObject value = localRecord(c.getString(0), c.getString(1));
            if (value != null) {
              JSONObject known = shadow(value.getString("record_id"));
              if (known == null
                  && value.getJSONArray("source_refs").length() == 0
                  && !value.getString("room_id").equals("manual")
                  && !excluded(value.getString("room_id"))) continue;
              queue(value, c.getString(0), "upsert");
            }
          }
        }
      }
      try (Cursor c =
          db.rawQuery(
              "SELECT id,local_id,payload FROM knowledge_shadow WHERE space=? AND local_id<>'' AND"
                  + " revision>0",
              new String[] {space})) {
        while (c.moveToNext()) {
          JSONObject value = new JSONObject(c.getString(2));
          String table = value.getString("kind").equals("memory") ? "memories" : "work_items";
          String key = table.equals("memories") ? "ref" : "id";
          try (Cursor nativeRow =
              db.rawQuery(
                  "SELECT 1 FROM " + table + " WHERE " + key + "=?",
                  new String[] {c.getString(1)})) {
            if (!value.optBoolean("deleted") && !nativeRow.moveToFirst())
              queue(strip(value), c.getString(1), "forget");
          }
        }
      }
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  JSONArray policies() throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c =
        db.rawQuery(
            "SELECT * FROM knowledge_rooms WHERE space=? AND desired IS NOT NULL LIMIT 100",
            new String[] {space})) {
      while (c.moveToNext()) {
        JSONObject r = row(c);
        result.put(
            new JSONObject()
                .put("operation_id", r.getString("operation"))
                .put("room_id", r.getString("room_id"))
                .put("expected_revision", r.getLong("revision"))
                .put("excluded", r.getInt("desired") == 1)
                .put("label", r.getString("label")));
      }
    }
    return result;
  }

  int conflictCount() {
    try (Cursor c =
        db.rawQuery(
            "SELECT count(*) FROM knowledge_shadow WHERE space=? AND conflict_id IS NOT NULL",
            new String[] {space})) {
      c.moveToFirst();
      return c.getInt(0);
    }
  }

  JSONArray mutations() throws Exception {
    return mutations(true);
  }

  JSONArray mutations(boolean allowHistory) throws Exception {
    JSONArray result = new JSONArray();
    int bytes = 0;
    try (Cursor c =
        db.rawQuery(
            "SELECT o.* FROM knowledge_outbox o JOIN knowledge_shadow s ON s.space=o.space AND"
                + " s.id=o.id WHERE o.space=? AND s.conflict_id IS NULL ORDER BY CASE WHEN"
                + " o.kind='source' THEN 0 ELSE 1 END,o.rowid DESC LIMIT 100",
            new String[] {space})) {
      while (c.moveToNext()) {
        JSONObject r = row(c), draft = new JSONObject(r.getString("payload"));
        if (draft.getString("kind").equals("source")
            && excluded(draft.getString("room_id"))
            && !r.getString("action").equals("forget")) continue;
        if (!allowHistory
            && draft.getString("kind").equals("source")
            && draft.getLong("occurred_at") < System.currentTimeMillis() - 30L * 86400000L
            && !r.getString("action").equals("forget")) {
          try (Cursor needed =
              db.rawQuery(
                  "SELECT 1 FROM knowledge_source_refs r JOIN knowledge_priority p ON"
                      + " p.space=r.space AND p.account_id=r.account_id AND p.msg_id=r.msg_id WHERE"
                      + " r.space=? AND r.id=? LIMIT 1",
                  new String[] {space, draft.getString("record_id")})) {
            if (!needed.moveToFirst()) continue;
          }
        }
        boolean evidenceReady = true;
        JSONArray refs = draft.getJSONArray("source_refs");
        for (int i = 0; i < refs.length(); i++) {
          JSONObject known = shadow(refs.getJSONObject(i).getString("record_id"));
          evidenceReady &=
              known != null
                  && known.optLong("revision") >= refs.getJSONObject(i).getLong("revision");
        }
        if (!evidenceReady) continue;
        int size = r.getString("payload").getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
        if (bytes + size > 800000 && result.length() > 0) break;
        bytes += size;
        result.put(
            new JSONObject()
                .put("operation_id", r.getString("operation"))
                .put("expected_revision", r.getLong("expected_revision"))
                .put("action", r.getString("action"))
                .put("record", draft));
        if (result.length() >= 30) break;
      }
    }
    return result;
  }

  void receiveReceipt(JSONObject receipt) throws Exception {
    String id = receipt.optString("record_id"), outcome = receipt.getString("status");
    if (id.isEmpty()) {
      String room = receipt.getString("room_id");
      if (outcome.equals("accepted"))
        db.execSQL(
            "UPDATE knowledge_rooms SET revision=?,remote_excluded=?,desired=NULL,operation=NULL"
                + " WHERE space=? AND room_id=? AND operation=?",
            new Object[] {
              receipt.getLong("revision"),
              receipt.optBoolean("excluded") ? 1 : 0,
              space,
              room,
              receipt.getString("operation_id")
            });
      // On policy conflict retain the local intention, but use a fresh operation and server
      // revision.
      else if (outcome.equals("conflict"))
        db.execSQL(
            "UPDATE knowledge_rooms SET revision=?,operation=? WHERE space=? AND room_id=? AND"
                + " operation=?",
            new Object[] {
              receipt.getLong("revision"),
              UUID.randomUUID().toString(),
              space,
              room,
              receipt.getString("operation_id")
            });
      return;
    }
    JSONObject pending = pending(id);
    if (pending == null
        || !pending.getString("operation").equals(receipt.getString("operation_id"))) return;
    if(receipt.optString("discarded").equals("forgotten_source")){db.delete("knowledge_outbox","space=? AND id=?",new String[]{space,id});applyRecord(receipt.getJSONObject("current"),true);return;}
    if (outcome.equals("accepted")) {
      JSONObject record = receipt.getJSONObject("record");
      db.execSQL(
          "UPDATE knowledge_shadow SET revision=?,hash=?,payload=?,conflict_id=NULL WHERE space=?"
              + " AND id=?",
          new Object[] {
            record.getLong("revision"), pending.getString("hash"), record.toString(), space, id
          });
      db.delete("knowledge_outbox", "space=? AND id=?", new String[] {space, id});
      if(record.optBoolean("deleted"))applyRecord(record,true);
    } else if (outcome.equals("conflict")) {
      db.execSQL(
          "UPDATE knowledge_shadow SET conflict_id=? WHERE space=? AND id=?",
          new Object[] {receipt.getString("conflict_id"), space, id});
    }
  }

  void applyRecord(JSONObject record, boolean resolved) throws Exception {
    String id = record.getString("record_id"), kind = record.getString("kind");
    JSONObject previous = shadow(id);
    if (previous != null
        && !resolved
        && record.getLong("revision") <= previous.getLong("revision")) {
      if (record.getLong("revision") == previous.getLong("revision")
          && pending(id) == null
          && previous.isNull("conflict_id")) queueAck(id, record.getLong("revision"));
      return;
    }
    if (!resolved && (pending(id) != null || previous != null && !previous.isNull("conflict_id")))
      return;
    if (resolved) db.delete("knowledge_outbox", "space=? AND id=?", new String[] {space, id});
    String local = previous == null ? "" : previous.optString("local_id", "");
    if (kind.equals("memory")
        && record.optBoolean("confirmed")
        && !record.getJSONObject("origin").optString("source_type").equals("formal_memory")) {
      if (local.isEmpty()) local = "remote:" + AnalysisContract.hash(id);
      if (record.optBoolean("deleted")) db.delete("memories", "ref=?", new String[] {local});
      else {
        JSONObject source =
            new JSONObject().put("_knowledge_id", id).put("content", record.getString("text"));
        try (Cursor c =
            db.rawQuery("SELECT source_json FROM memories WHERE ref=?", new String[] {local})) {
          if (c.moveToFirst()) source = new JSONObject(c.getString(0));
        }
        db.execSQL(
            "INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?,?)",
            new Object[] {
              local,
              record.getString("title"),
              "fact",
              record.getString("text"),
              (source.has("_information_summary")
                      ? source.put("_information_summary", record.getString("text"))
                      : source)
                  .toString(),
              record.getLong("occurred_at"),
              System.currentTimeMillis()
            });
      }
    } else if (kind.matches("todo|event") && record.optBoolean("deleted")) {
      if (local.isEmpty()) local = id;
      JSONObject previousWork = new OrganizerStore(memory).work(local);
      OrganizerStore.replaceReminder(
          db,
          local,
          previousWork == null ? 1 : previousWork.getInt("revision") + 1,
          null,
          System.currentTimeMillis());
      db.delete("work_items", "id=?", new String[] {local});
      db.delete("work_changes", "work_id=?", new String[] {local});
    } else if (kind.matches("todo|event") && record.optBoolean("confirmed")) {
      if (local.isEmpty()) local = id;
      JSONObject old = new OrganizerStore(memory).work(local);
      int revision = old == null ? 1 : old.getInt("revision") + 1;
      JSONObject occurrence=record.getJSONObject("origin").optJSONObject("reminder_occurrence");
      boolean anotherDevice=occurrence!=null&&!occurrence.isNull("primary_device_id")&&!device().equals(occurrence.getString("primary_device_id"));

      String state = record.optBoolean("deleted") ? "cancelled" : record.getString("state");
      if (state.equals("active")) state = "open";
      ContentValues v = new ContentValues();
      v.put("id", local);
      v.put("kind", kind);
      v.put("title", record.getString("title"));
      v.put("state", state);
      v.put("details", record.getString("text"));
      v.put("revision", revision);
      v.put("timezone", record.getString("timezone"));
      if(record.isNull("due_date"))v.putNull("due_date");else v.put("due_date",record.getString("due_date"));
      v.put("updated_at", System.currentTimeMillis());
      for (String key : new String[] {"due_at", "starts_at", "ends_at"}) {
        if (record.isNull(key)) v.putNull(key);
        else v.put(key, record.getLong(key));
      }
      if (old == null) {
        v.put("involvement", "self");
        v.put("account_id", "remote");
        v.put("talker", record.getString("room_id"));
        v.put(
            "source_json",
            new JSONObject()
                .put("_knowledge_id", id)
                .put("content", record.getString("text"))
                .toString());
        v.put("created_at", record.getLong("occurred_at"));
        db.insertOrThrow("work_items", null, v);
      } else db.update("work_items", v, "id=?", new String[] {local});
      OrganizerStore.replaceReminder(
          db,
          local,
          revision,
          state.matches("completed|cancelled")||anotherDevice
              ? null
              : OrganizerStore.nullable(record, "remind_at"),
          System.currentTimeMillis());
      db.execSQL(
          "INSERT OR IGNORE INTO work_changes VALUES(?,?,?,?,?,?)",
          new Object[] {
            "knowledge:" + id + ":" + record.getLong("revision"),
            local,
            "sync",
            revision,
            new JSONObject()
                .put("record_id", id)
                .put("revision", record.getLong("revision"))
                .toString(),
            System.currentTimeMillis()
          });
    }
    // Install the canonical template before projecting the native fingerprint. On first import,
    // localRecord needs this template to preserve remote room, origin and evidence identities.
    db.execSQL(
        "INSERT OR REPLACE INTO knowledge_shadow VALUES(?,?,?,?,?,?,NULL)",
        new Object[] {
          space,
          id,
          record.getLong("revision"),
          KnowledgeWire.fingerprint(record),
          record.toString(),
          local
        });
    JSONObject nativeValue = local.isEmpty() ? null : localRecord(local, kind);
    if (nativeValue != null)
      db.execSQL(
          "UPDATE knowledge_shadow SET hash=? WHERE space=? AND id=?",
          new Object[] {KnowledgeWire.fingerprint(nativeValue), space, id});
    queueAck(id, record.getLong("revision"));
  }

  void applyPull(JSONObject batch) throws Exception {
    db.beginTransactionNonExclusive();
    try {
      JSONArray policies = batch.getJSONArray("policies");
      for (int i = 0; i < policies.length(); i++) {
        JSONObject p = policies.getJSONObject(i);
        String room = p.getString("room_id");
        db.execSQL(
            "INSERT OR IGNORE INTO knowledge_rooms(space,room_id,account_id,talker,label)"
                + " VALUES(?,?,'','',?)",
            new Object[] {space, room, p.getString("label")});
        db.execSQL(
            "UPDATE knowledge_rooms SET revision=CASE WHEN desired IS NULL THEN ? ELSE revision"
                + " END,remote_excluded=? WHERE space=? AND room_id=?",
            new Object[] {p.getLong("revision"), p.getBoolean("excluded") ? 1 : 0, space, room});
        try (Cursor c =
            db.rawQuery(
                "SELECT * FROM knowledge_rooms WHERE space=? AND room_id=?",
                new String[] {space, room})) {
          if (c.moveToFirst()) {
            JSONObject r = row(c);
            if (r.isNull("desired") && !r.getString("account_id").isEmpty()) {
              ConversationPolicy localPolicy = new ConversationPolicy(memory);
              // Replayed policy snapshots must not overwrite local display names or timestamps.
              if (localPolicy.excluded(r) != p.getBoolean("excluded"))
                localPolicy.apply(
                    db,
                    r.put("conversation_title", p.getString("label")),
                    p.getBoolean("excluded"));
            }
          }
        }
      }
      JSONArray changes = batch.getJSONArray("changes");
      for (int i = 0; i < changes.length(); i++) {
        JSONObject change = changes.getJSONObject(i), value = change.getJSONObject("value");
        if (change.getString("kind").equals("record")) applyRecord(value, false);
        if (change.getString("kind").equals("conflict_resolved")) {
          JSONObject record = value.getJSONObject("record"),
              old = shadow(record.getString("record_id"));
          if (old != null && value.getString("conflict_id").equals(old.optString("conflict_id"))) {
            JSONObject flight = pending(record.getString("record_id"));
            JSONObject local =
                old.optString("local_id", "").isEmpty()
                    ? null
                    : localRecord(old.getString("local_id"), record.getString("kind"));
            if (flight != null
                && local != null
                && !KnowledgeWire.fingerprint(local).equals(flight.getString("hash"))) {
              // A newer offline edit happened after the conflicted edit. Preserve it and rebase.
              db.delete(
                  "knowledge_outbox",
                  "space=? AND id=?",
                  new String[] {space, record.getString("record_id")});
              db.execSQL(
                  "UPDATE knowledge_shadow SET revision=?,payload=?,hash='',conflict_id=NULL WHERE"
                      + " space=? AND id=?",
                  new Object[] {
                    record.getLong("revision"),
                    record.toString(),
                    space,
                    record.getString("record_id")
                  });
            } else applyRecord(record, true);
          }
        }
      }
      meta("epoch", batch.getLong("policy_epoch"));
      meta("cursor", batch.getLong("cursor"));
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  private void queueAck(String id, long revision) {
    db.execSQL(
        "INSERT OR REPLACE INTO knowledge_ack_outbox VALUES(?,?,?)",
        new Object[] {space, id, revision});
  }

  JSONArray applied() throws Exception {
    JSONArray result = new JSONArray();
    try (Cursor c =
        db.rawQuery(
            "SELECT a.id,a.revision FROM knowledge_ack_outbox a JOIN knowledge_shadow s ON"
                + " s.space=a.space AND s.id=a.id WHERE a.space=? AND s.conflict_id IS NULL AND NOT"
                + " EXISTS(SELECT 1 FROM knowledge_outbox o WHERE o.space=a.space AND o.id=a.id)"
                + " LIMIT 100",
            new String[] {space})) {
      while (c.moveToNext())
        result.put(new JSONObject().put("record_id", c.getString(0)).put("revision", c.getLong(1)));
    }
    return result;
  }

  void acknowledged(JSONArray applied) throws Exception {
    db.beginTransactionNonExclusive();
    try {
      for (int i = 0; i < applied.length(); i++) {
        JSONObject ref = applied.getJSONObject(i);
        db.delete(
            "knowledge_ack_outbox",
            "space=? AND id=? AND revision<=?",
            new String[] {space, ref.getString("record_id"), ref.getString("revision")});
      }
      db.setTransactionSuccessful();
    } finally {
      db.endTransaction();
    }
  }

  JSONObject localSource(String id) throws Exception {
    if (!id.matches("wechat:[0-9a-f]{64}:part:[0-9]+")) return null;
    try (Cursor c =
        db.rawQuery(
            "SELECT k.source_json FROM knowledge_sources k JOIN knowledge_source_refs r"
                + " ON r.space=k.space AND r.account_id=k.account_id AND r.msg_id=k.msg_id"
                + " WHERE k.space=? AND r.id=? LIMIT 1",
            new String[] {space, id})) {
      return c.moveToFirst() ? new JSONObject(c.getString(0)) : null;
    }
  }
}
