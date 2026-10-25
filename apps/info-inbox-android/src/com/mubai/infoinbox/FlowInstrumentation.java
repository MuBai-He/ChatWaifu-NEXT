package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.Context;
import android.database.sqlite.SQLiteDatabase;
import android.os.Bundle;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONArray;
import org.json.JSONObject;

/** Real Android SQLite scenarios use disposable databases, never the WeChat archive. */
public final class FlowInstrumentation extends Instrumentation {
  private Bundle arguments;
  private Context context;
  private MemoryStore memory;
  private int checks;
  private static final String NAME = "organizer_fixture.db";

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    arguments = args;
    start();
  }

  private void check(boolean value, String name) {
    if (!value) throw new AssertionError(name);
    checks++;
  }

  private OrganizerStore fresh() {
    if (memory != null) memory.close();
    context.deleteDatabase(NAME);
    memory = new MemoryStore(context, NAME);
    return new OrganizerStore(memory);
  }

  private JSONObject source(long id, String text) throws Exception {
    return new JSONObject()
        .put("account_id", "fixture-account")
        .put("talker", "fixture-room")
        .put("conversation_title", "合成会话")
        .put("msg_id", id)
        .put("type", 1)
        .put("is_send", 1)
        .put("created_at", 1700000000000L + id)
        .put("content", text)
        .put("server_id", "9223372036854775806");
  }

  private JSONObject modelItem(JSONObject source, String title, String kind) throws Exception {
    return new JSONObject()
        .put("title", title)
        .put("kind", kind)
        .put("importance", "high")
        .put("reason", "合成输入中的明确约定")
        .put("involvement", "self")
        .put("source_refs", new JSONArray().put(MemoryStore.reference(source)));
  }

  private JSONObject response(JSONObject batch, JSONArray raw, JSONArray targets) throws Exception {
    Map<String, JSONObject> allowed = new LinkedHashMap<>(), known = new LinkedHashMap<>();
    JSONArray refs = new JSONArray();
    JSONArray sources = batch.getJSONArray("sources");
    for (int i = 0; i < sources.length(); i++) {
      JSONObject source = sources.getJSONObject(i);
      String ref = MemoryStore.reference(source);
      allowed.put(ref, source);
      refs.put(ref);
    }
    for (int i = 0; i < targets.length(); i++) {
      JSONObject target = targets.getJSONObject(i);
      known.put(target.getString("id"), target);
    }
    return new JSONObject()
        .put("items", AnalysisContract.validate(raw, allowed, known, "Asia/Shanghai"))
        .put("processed_refs", refs);
  }

  private JSONObject stage(
      OrganizerStore store,
      long message,
      String title,
      String kind,
      String operation,
      JSONObject target,
      Long due)
      throws Exception {
    store.enqueue(new JSONArray().put(source(message, "合成资料 " + title)));
    long now = System.currentTimeMillis();
    JSONObject batch = store.claim(now);
    JSONObject item =
        modelItem(batch.getJSONArray("sources").getJSONObject(0), title, kind)
            .put("operation", operation);
    if (target != null)
      item.put("target_id", target.getString("id"))
          .put("expected_revision", target.getInt("revision"));
    if (due != null) item.put("due_at", TimeRules.iso(due));
    JSONObject result =
        response(
            batch,
            new JSONArray().put(item),
            target == null ? new JSONArray() : new JSONArray().put(target));
    store.finish(batch, result, now + 1);
    return result.getJSONArray("items").getJSONObject(0);
  }

  private JSONObject edited(String title, Long due) throws Exception {
    return new JSONObject()
        .put("title", title)
        .put("timezone", "Asia/Shanghai")
        .put("involvement", "self")
        .put("state", "open")
        .put("due_at", due == null ? JSONObject.NULL : due)
        .put("starts_at", JSONObject.NULL)
        .put("ends_at", JSONObject.NULL);
  }

  @Override
  public void onStart() {
    Bundle output = new Bundle();
    context = getTargetContext();
    boolean passed = false;
    try {
      if (arguments != null && "true".equals(arguments.getString("exclusions_status"))) {
        try (MemoryStore live = new MemoryStore(context)) {
          ConversationPolicy policy = new ConversationPolicy(live);
          RootGateway root = new RootGateway(context);
          JSONArray pending = root.pendingSources(live, new OrganizerStore(live).intakeFloor());
          for (int i = 0; i < pending.length(); i++)
            check(
                !policy.excluded(pending.getJSONObject(i)), "live intake excludes selected rooms");
          JSONArray prior = root.contextBefore(pending);
          for (int i = 0; i < prior.length(); i++)
            check(!policy.excluded(prior.getJSONObject(i)), "live context excludes selected rooms");
          JSONArray rooms = root.policyConversations("");
          check(rooms.length() > 0 && rooms.length() <= 100, "live contact manager bounded");
          root.policyConversations("filehelper");
          root.policyConversations("'fixture-no-match");
          JSONArray clues = root.candidates(live);
          for (int i = 0; i < clues.length(); i++)
            check(!policy.excluded(clues.getJSONObject(i)), "excluded clues hidden");
          JSONArray visibleRooms = root.conversations(live, "", false);
          for (int i = 0; i < visibleRooms.length(); i++)
            check(
                !policy.excluded(visibleRooms.getJSONObject(i)),
                "excluded rooms hidden by default");
          JSONArray hiddenRooms = root.conversations(live, "", true);
          for (int i = 0; i < hiddenRooms.length(); i++)
            check(policy.excluded(hiddenRooms.getJSONObject(i)), "separate excluded room view");
          root.conversations(live, "'fixture-no-match", false);
          output.putInt("visible_rooms", visibleRooms.length());
          output.putInt("excluded_rooms", hiddenRooms.length());
          output.putInt("inbox_clues", clues.length());
          output.putString(
              "result", "passed: live root intake, context, contacts and inbox visibility policy");
          output.putInt("selected_exclusions", policy.list().length());
          output.putInt("allowed_pending", pending.length());
          output.putInt("prior_context", prior.length());
        }
        passed = true;
      } else if (arguments != null && "true".equals(arguments.getString("status_only"))) {
        ApiSettings settings = new ApiSettings(context);
        output.putString(
            "result",
            new JSONObject()
                .put("configured", settings.ready())
                .put("automatic", settings.automatic())
                .put("timezone", settings.timezone())
                .put("notifications", ReminderScheduler.notificationsAllowed(context))
                .put("precise_reminders", ReminderScheduler.preciseAllowed(context))
                .toString());
        passed = true;
      } else {
        migration();
        exclusions();
        bulkExclusions();
        queueAndRecovery();
        sourceCorrections();
        freshPriorityAndProgress();
        workAndDelivery();
        concurrentClaims();
        output.putString(
            "result",
            "passed: "
                + checks
                + " assertions covering schema migration, long-message progress, atomic processing,"
                + " bounded retry, restart and lease fencing, corrected sources, task revisions,"
                + " rescheduling/cancellation, reminders, concurrent claims and AI exclusions");
        passed = true;
      }
    } catch (Throwable error) {
      output.putString(
          "result", "failed: " + error.getClass().getSimpleName() + " " + error.getMessage());
    } finally {
      if (memory != null) memory.close();
      context.deleteDatabase(NAME);
      context.deleteDatabase("migration_fixture.db");
    }
    finish(passed ? -1 : 0, output);
  }

  private void migration() throws Exception {
    context.deleteDatabase("migration_fixture.db");
    SQLiteDatabase db = context.openOrCreateDatabase("migration_fixture.db", 0, null);
    db.execSQL(
        "CREATE TABLE memories(ref TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,quote"
            + " TEXT NOT NULL,source_json TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at"
            + " INTEGER NOT NULL)");
    db.execSQL("CREATE TABLE source_states(ref TEXT PRIMARY KEY,state TEXT NOT NULL)");
    db.execSQL(
        "CREATE TABLE ai_processed(account_id TEXT NOT NULL,msg_id INTEGER NOT NULL,PRIMARY"
            + " KEY(account_id,msg_id))");
    db.execSQL(
        "CREATE TABLE ai_reviews(ref TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT"
            + " NULL,importance TEXT NOT NULL,reason TEXT NOT NULL,item_json TEXT NOT"
            + " NULL,created_at INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending')");
    db.execSQL(
        "INSERT INTO memories VALUES('fixture-old','保留的记忆','记忆','原文',?,1,1)",
        new Object[] {source(1, "原文").toString()});
    db.setVersion(2);
    db.close();
    try (MemoryStore migrated = new MemoryStore(context, "migration_fixture.db")) {
      check(migrated.getWritableDatabase().getVersion() == 4, "schema version");
      check(
          migrated.list().length() == 1
              && migrated.list().getJSONObject(0).getString("title").equals("保留的记忆"),
          "preserved confirmed memory");
      check(new OrganizerStore(migrated).queueStats().length() == 0, "new queue created");
      check(new ConversationPolicy(migrated).list().length() == 0, "v2 exclusion table migrated");
      new OrganizerStore(migrated).enqueue(new JSONArray().put(source(90, "迁移前队列")));
      migrated.getWritableDatabase().execSQL("DROP TABLE conversation_exclusions");
      migrated.getWritableDatabase().setVersion(3);
    }
    try (MemoryStore migrated = new MemoryStore(context, "migration_fixture.db")) {
      check(migrated.getWritableDatabase().getVersion() == 4, "v3 upgraded to v4");
      check(migrated.list().length() == 1, "v3 memory retained");
      check(new OrganizerStore(migrated).queueStats().optInt("pending") == 1, "v3 queue retained");
      check(new ConversationPolicy(migrated).list().length() == 0, "v3 starts with no exclusions");
    }
  }

  private void exclusions() throws Exception {
    OrganizerStore store = fresh();
    ConversationPolicy policy = new ConversationPolicy(memory);
    long now = System.currentTimeMillis();
    JSONObject group = source(601, "保留的群聊原文").put("talker", "fixture@chatroom");
    JSONObject friend = source(602, "保留的好友原文").put("talker", "fixture-friend");
    JSONObject otherAccount = new JSONObject(group.toString()).put("account_id", "second-account");
    policy.set(group, true);
    check(policy.excluded(group), "group excluded");
    check(
        policy.excluded(new JSONObject(group.toString()).put("conversation_title", "改备注")),
        "rename keeps identity");
    check(!policy.excluded(otherAccount), "account isolation");
    policy.set(friend, true);
    check(policy.list().length() == 2, "groups and friends supported");
    check(store.enqueue(new JSONArray().put(group).put(friend)) == 0, "excluded intake refused");
    check(
        store.sourceProgress(group).getString("state").equals("excluded"),
        "excluded progress visible");
    check(group.getString("content").equals("保留的群聊原文"), "raw input unchanged");
    check(store.enqueue(new JSONArray().put(otherAccount)) == 1, "other account still intake");
    JSONObject batch = store.claim(now);
    check(batch.getJSONArray("sources").length() == 1, "only allowed room claimed");
    store.finish(batch, response(batch, new JSONArray(), new JSONArray()), now + 1);
    policy.set(group, false);
    check(!policy.excluded(group) && policy.excluded(friend), "independent restore");
    check(store.enqueue(new JSONArray().put(group)) == 1, "restored archive can intake");
    JSONObject unaffected = source(603, "另一个会话");
    store.enqueue(new JSONArray().put(unaffected));
    batch = store.claim(now + 2);
    check(batch.getJSONArray("sources").length() == 2, "mixed batch claimed");
    JSONObject lateResult = response(batch, new JSONArray(), new JSONArray());
    AnalysisSendGate gate = policy.gate(store, batch);
    AtomicInteger writes = new AtomicInteger();
    gate.send(writes::incrementAndGet);
    check(writes.get() == 1, "allowed upload gate writes once");
    policy.set(group, true);
    check(store.queueStats().optInt("excluded") == 1, "queued group paused");
    check(store.queueStats().optInt("pending") == 1, "other room recovered without retries");
    boolean refused = false;
    try {
      gate.send(writes::incrementAndGet);
    } catch (IllegalStateException expected) {
      refused = true;
    }
    check(refused && writes.get() == 1, "exclude before send blocks body write");
    policy.set(group, false);
    refused = false;
    try {
      store.finish(batch, lateResult, now + 3);
    } catch (IllegalStateException expected) {
      refused = true;
    }
    check(refused, "exclude then restore fences old in-flight result");
    store.fail(batch.getString("id"), now + 4, false);
    check(store.queueStats().optInt("pending") == 2, "late failure does not change restored queue");
    batch = store.claim(now + 5);
    JSONObject chosen = null;
    for (int i = 0; i < batch.getJSONArray("sources").length(); i++) {
      JSONObject s = batch.getJSONArray("sources").getJSONObject(i);
      if (s.getString("talker").equals(group.getString("talker"))) chosen = s;
    }
    check(chosen != null, "restored room claimed again");
    JSONObject result =
        response(
            batch,
            new JSONArray()
                .put(modelItem(chosen, "合成任务", "todo"))
                .put(modelItem(chosen, "合成记忆", "memory")),
            new JSONArray());
    store.finish(batch, result, now + 6);
    check(memory.suggestions().length() == 2, "allowed proposals shown");
    policy.set(group, true);
    check(memory.suggestions().length() == 0, "existing pending AI proposals hidden");
    check(store.suggestionsForSource(group).length() == 0, "source suggestions hidden");
    refused = false;
    try {
      store.confirm(
          result.getJSONArray("items").getJSONObject(0), edited("合成任务", null), null, now + 7);
    } catch (IllegalStateException expected) {
      refused = true;
    }
    check(refused, "open task proposal cannot confirm after exclude");
    refused = false;
    try {
      store.confirmMemory(result.getJSONArray("items").getJSONObject(1), "合成记忆", "记忆");
    } catch (IllegalStateException expected) {
      refused = true;
    }
    check(refused, "open memory proposal cannot confirm after exclude");
    memory.save(group, "手动保存的原文", "记忆");
    JSONObject manual = store.manual(group, "todo", "手动事项");
    String id = store.confirm(manual, edited("手动事项", null), null, now + 8);
    check(
        memory.list().length() == 1 && store.work(id) != null,
        "manual local organization remains available");
    JSONObject edit = store.editProposal(store.work(id));
    store.confirm(edit, edited("手动修改事项", null), null, now + 9);
    check(
        store.work(id).getString("title").equals("手动修改事项"),
        "excluded confirmed tasks remain editable");
    // Exercise both real HTTP adapters against a local unused listener. The fence must run
    // before getOutputStream; excluded records must never even open a connection.
    try (java.net.ServerSocket listener =
        new java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1"))) {
      String base = "http://127.0.0.1:" + listener.getLocalPort();
      AtomicInteger denied = new AtomicInteger();
      AnalysisSendGate deny =
          write -> {
            denied.incrementAndGet();
            throw new IllegalStateException("fixture excluded");
          };
      refused = false;
      try {
        new OpenAiClient(base, "fixture", "", false)
            .analyze(
                new JSONArray().put(group),
                new JSONArray(),
                new JSONArray(),
                "Asia/Shanghai",
                deny);
      } catch (IllegalStateException expected) {
        refused = true;
      }
      check(refused && denied.get() == 1, "standalone adapter honors upload fence");
      String prefs = "exclusion_backend_fixture", alias = "info-inbox-exclusion-fixture";
      try {
        BackendSettings backend = new BackendSettings(context, prefs, alias);
        backend.save(base, "fixture-token-with-no-production-authority");
        backend.connected(java.util.UUID.randomUUID().toString());
        refused = false;
        try {
          backend
              .client()
              .analyze(
                  "fixture-request",
                  new JSONArray().put(group),
                  new JSONArray(),
                  new JSONArray(),
                  "Asia/Shanghai",
                  deny);
        } catch (IllegalStateException expected) {
          refused = true;
        }
        check(refused && denied.get() == 2, "runtime adapter honors upload fence");
      } finally {
        context.getSharedPreferences(prefs, Context.MODE_PRIVATE).edit().clear().commit();
        java.security.KeyStore keys = java.security.KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        if (keys.containsAlias(alias)) keys.deleteEntry(alias);
      }
      listener.setSoTimeout(300);
      boolean connected = false;
      try (java.net.Socket unexpected = listener.accept()) {
        connected = true;
      } catch (java.net.SocketTimeoutException expected) {
      }
      check(!connected, "excluded data never opens local HTTP connection in either adapter");
    }
    memory.close();
    memory = new MemoryStore(context, NAME);
    store = new OrganizerStore(memory);
    policy = new ConversationPolicy(memory);
    check(policy.excluded(group) && policy.excluded(friend), "exclusions survive restart");
    store.recoverInterruptedRuns();
    store.retryFailed();
    check(store.claim(now + 500000L) == null, "restart and retry cannot bypass exclusions");
    policy.set(group, false);
    check(memory.suggestions().length() == 2, "restore makes prior pending proposals available");
    check(
        memory.list().length() == 1 && store.workItems(false).length() == 1,
        "confirmed records retained");
    check(store.enqueue(new JSONArray().put(group)) == 0, "completed progress retained on restore");
    store = fresh();
    policy = new ConversationPolicy(memory);
    store.enqueue(new JSONArray().put(group).put(friend));
    policy.set(group, true);
    // Even a stale pending row cannot bypass the claim policy or starve another room.
    memory.getWritableDatabase().execSQL("UPDATE queued_sources SET state='pending'");
    batch = store.claim(now + 10);
    check(
        batch.getJSONArray("sources").length() == 1
            && batch
                .getJSONArray("sources")
                .getJSONObject(0)
                .getString("talker")
                .equals(friend.getString("talker")),
        "claim exclusion enforced before limit for stale queue rows");
  }

  private void bulkExclusions() throws Exception {
    OrganizerStore store = fresh();
    ConversationPolicy policy = new ConversationPolicy(memory);
    long now = System.currentTimeMillis();
    JSONObject a = source(701, "合成群资料").put("talker", "bulk@chatroom");
    JSONObject b = source(702, "合成好友资料").put("talker", "bulk-friend");
    JSONObject other = source(703, "其他会话资料");
    store.enqueue(new JSONArray().put(a).put(b).put(other));
    JSONObject old = store.claim(now);
    JSONObject oldResult = response(old, new JSONArray(), new JSONArray());
    int changed = policy.setMany(new JSONArray().put(a).put(b).put(a), true);
    check(changed == 2 && policy.count() == 2, "bulk deduplicates identical identities");
    check(
        policy.excluded(a) && policy.excluded(b) && !policy.excluded(other),
        "bulk targets only selected rooms");
    check(
        store.queueStats().optInt("excluded") == 2 && store.queueStats().optInt("pending") == 1,
        "bulk queue changes committed together");
    boolean refused = false;
    try {
      store.finish(old, oldResult, now + 1);
    } catch (IllegalStateException expected) {
      refused = true;
    }
    check(refused, "bulk exclusion fences old batch");
    JSONObject allowed = store.claim(now + 2);
    check(
        allowed.getJSONArray("sources").length() == 1
            && allowed.getJSONArray("sources").getJSONObject(0).getLong("msg_id") == 703,
        "unselected room proceeds");
    store.finish(allowed, response(allowed, new JSONArray(), new JSONArray()), now + 3);
    changed = policy.setMany(new JSONArray().put(a).put(b), false);
    check(changed == 2 && policy.count() == 0, "bulk restoration committed together");
    check(
        store.queueStats().optInt("pending") == 2 && store.queueStats().optInt("done") == 1,
        "bulk restore preserves completed progress");
    refused = false;
    try {
      policy.setMany(new JSONArray().put(a).put(new JSONObject()), true);
    } catch (IllegalArgumentException expected) {
      refused = true;
    }
    check(
        refused && policy.count() == 0 && store.queueStats().optInt("pending") == 2,
        "invalid selection causes no partial policy or queue writes");
    // A database failure after the first row must also roll back the entire batch.
    memory
        .getWritableDatabase()
        .execSQL(
            "CREATE TRIGGER bulk_failure BEFORE INSERT ON conversation_exclusions "
                + "WHEN NEW.talker='bulk-friend' BEGIN SELECT RAISE(ABORT,'fixture'); END");
    refused = false;
    try {
      policy.setMany(new JSONArray().put(a).put(b), true);
    } catch (android.database.sqlite.SQLiteException expected) {
      refused = true;
    }
    check(refused && policy.count() == 0, "database error rolls back earlier policy changes");
    check(
        store.queueStats().optInt("pending") == 2,
        "database error rolls back earlier queue changes");
    memory.getWritableDatabase().execSQL("DROP TRIGGER bulk_failure");
    refused = false;
    try {
      policy.setMany(new JSONArray(), true);
    } catch (IllegalArgumentException expected) {
      refused = true;
    }
    check(refused, "empty bulk operation rejected");
    JSONArray oversized = new JSONArray();
    for (int i = 0; i < 101; i++) oversized.put(a);
    refused = false;
    try {
      policy.setMany(oversized, true);
    } catch (IllegalArgumentException expected) {
      refused = true;
    }
    check(refused && policy.count() == 0, "oversized bulk operation rejected without writes");
    policy.setMany(new JSONArray().put(a).put(b), true);
    policy.setMany(new JSONArray().put(a).put(b), true);
    check(policy.count() == 2, "repeated bulk confirmation is idempotent");
    memory.close();
    memory = new MemoryStore(context, NAME);
    check(new ConversationPolicy(memory).count() == 2, "bulk policy survives restart");
  }

  private void freshPriorityAndProgress() throws Exception {
    OrganizerStore store = fresh();
    long now = System.currentTimeMillis();
    JSONObject latest = source(1002, "最新消息").put("created_at", now + 2);
    check(
        store.sourceProgress(latest).getString("state").equals("not_enqueued"),
        "not yet queued visible");
    JSONArray input = new JSONArray();
    for (int i = 1; i <= 50; i++)
      input.put(source(i, "历史资料").put("created_at", now - 2L * 86400000L + i));
    input.put(source(1001, "新消息").put("created_at", now + 1)).put(latest);
    store.enqueue(input);
    check(store.sourceProgress(latest).getString("state").equals("pending"), "pending visible");
    JSONObject batch = store.claim(now);
    JSONArray chosen = batch.getJSONArray("sources");
    check(chosen.length() == 30, "bounded recent batch");
    check(
        chosen.getJSONObject(0).getLong("msg_id") == 23,
        "older backlog cannot displace new source");
    check(
        chosen.getJSONObject(29).getLong("msg_id") == 1002,
        "newest message selected ahead of history");
    for (int i = 1; i < chosen.length(); i++)
      check(
          chosen.getJSONObject(i - 1).getLong("created_at")
              <= chosen.getJSONObject(i).getLong("created_at"),
          "selected records sent chronologically");
    check(
        store.sourceProgress(latest).getString("state").equals("processing"), "processing visible");
    store.finish(batch, response(batch, new JSONArray(), new JSONArray()), now + 3);
    JSONObject progress = store.sourceProgress(latest);
    check(
        progress.getString("state").equals("done") && progress.getInt("suggestions") == 0,
        "processed without suggestion distinguished from waiting");
    check(store.queueStats().getInt("pending") == 22, "history retained for subsequent batches");
    JSONObject actionable = source(1003, "需要准备资料").put("created_at", now + 3);
    store.enqueue(new JSONArray().put(actionable));
    batch = store.claim(now + 4);
    JSONObject selected =
        batch.getJSONArray("sources").getJSONObject(batch.getJSONArray("sources").length() - 1);
    check(selected.getLong("msg_id") == 1003, "new actionable source selected");
    store.finish(
        batch,
        response(batch, new JSONArray().put(modelItem(selected, "准备资料", "todo")), new JSONArray()),
        now + 5);
    check(store.sourceProgress(actionable).getInt("suggestions") == 1, "source suggestion count");
    check(store.suggestionsForSource(actionable).length() == 1, "source-filtered suggestion");
    check(store.suggestionsForSource(latest).length() == 0, "unrelated suggestion not included");
    JSONObject changed = new JSONObject(actionable.toString()).put("content", "改为准备另一份资料");
    check(
        store.sourceProgress(changed).getString("state").equals("changed"),
        "source correction visible before intake");
    check(store.suggestionsForSource(changed).length() == 0, "stale source suggestion hidden");
    store.enqueue(new JSONArray().put(changed));
    check(
        store.sourceProgress(changed).getString("state").equals("pending"),
        "corrected source queued");
    check(
        store.sourceProgress(changed).getInt("suggestions") == 0,
        "old revision suggestion excluded");
    JSONObject interrupted = store.claim(now + 6);
    check(interrupted != null, "claim before simulated process exit");
    store.recoverInterruptedRuns();
    check(
        store.sourceProgress(changed).getString("state").equals("pending"),
        "process restart recovers pending source immediately");
    JSONObject resumed = store.claim(now + 8);
    check(resumed != null, "process restart does not wait six-minute lease");
    boolean fenced = false;
    try {
      store.finish(interrupted, response(interrupted, new JSONArray(), new JSONArray()), now + 9);
    } catch (IllegalStateException expected) {
      fenced = true;
    }
    check(fenced, "result from previous process lease cannot commit");
    store.finish(resumed, response(resumed, new JSONArray(), new JSONArray()), now + 10);
    check(
        store.sourceProgress(changed).getString("state").equals("done"),
        "resumed batch finishes normally");
  }

  private void queueAndRecovery() throws Exception {
    OrganizerStore store = fresh();
    StringBuilder text = new StringBuilder();
    for (int i = 0; i < 10001; i++) text.append("消息");
    text.append("😀尾部截止信息");
    JSONObject source = source(2, text.toString());
    int segments = store.enqueue(new JSONArray().put(source));
    check(segments > 5, "long messages split");
    check(store.enqueue(new JSONArray().put(source)) == 0, "idempotent intake");
    long now = System.currentTimeMillis();
    JSONObject batch = store.claim(now);
    check(batch != null, "claim");
    check(store.claim(now + 1) == null, "single active durable lease");
    StringBuilder first = new StringBuilder();
    JSONArray parts = batch.getJSONArray("sources");
    for (int i = 0; i < parts.length(); i++)
      first.append(parts.getJSONObject(i).getString("content"));
    check(first.length() <= 16000, "bounded model input");
    store.finish(batch, response(batch, new JSONArray(), new JSONArray()), now + 2);
    check(store.queueStats().optInt("pending") > 0, "unread tail remains pending");
    memory.close();
    memory = new MemoryStore(context, NAME);
    store = new OrganizerStore(memory);
    JSONObject rest = store.claim(now + 3);
    StringBuilder tail = new StringBuilder();
    for (int i = 0; i < rest.getJSONArray("sources").length(); i++)
      tail.append(rest.getJSONArray("sources").getJSONObject(i).getString("content"));
    // Queue refs sort lexically by part number, but each batch preserves explicit segment identity.
    java.util.Map<Integer, String> all = new java.util.TreeMap<>();
    for (JSONObject b : new JSONObject[] {batch, rest})
      for (int i = 0; i < b.getJSONArray("sources").length(); i++) {
        JSONObject p = b.getJSONArray("sources").getJSONObject(i);
        all.put(p.getInt("segment"), p.getString("content"));
      }
    StringBuilder joined = new StringBuilder();
    for (String value : all.values()) joined.append(value);
    check(joined.toString().equals(text.toString()), "entire Unicode text retained after restart");
    store.finish(rest, response(rest, new JSONArray(), new JSONArray()), now + 4);
    check(store.queueStats().optInt("done") == segments, "all segments committed");
    store = fresh();
    store.enqueue(new JSONArray().put(source(3, "需要确认的资料")));
    batch = store.claim(now);
    JSONObject expired = store.claim(now + 360001L);
    check(expired != null, "expired lease reclaimed after restart");
    boolean refused = false;
    try {
      store.finish(batch, response(batch, new JSONArray(), new JSONArray()), now + 360002L);
    } catch (Exception expected) {
      refused = true;
    }
    check(refused, "late old result fenced");
    store.fail(expired.getString("id"), now + 360003L, false);
    check(store.queueStats().optInt("pending") == 1, "failed request retains input");
    for (int i = 1; i < 5; i++) {
      long time = now + 360003L + i * 60001L;
      JSONObject retry = store.claim(time);
      check(retry != null, "retry available");
      store.fail(retry.getString("id"), time, false);
    }
    check(store.queueStats().optInt("failed") == 1, "bounded retry dead letter");
    store.retryFailed();
    check(store.queueStats().optInt("pending") == 1, "manual retry retains source");
  }

  private void sourceCorrections() throws Exception {
    OrganizerStore store = fresh();
    long now = System.currentTimeMillis();
    store.enqueue(new JSONArray().put(source(4, "周四开会")));
    JSONObject old = store.claim(now);
    store.enqueue(new JSONArray().put(source(4, "改为周五开会")));
    boolean refused = false;
    try {
      store.finish(old, response(old, new JSONArray(), new JSONArray()), now + 1);
    } catch (Exception expected) {
      refused = true;
    }
    check(refused, "changed source invalidates in-flight result");
    store.fail(old.getString("id"), now + 2, true);
    check(store.claim(now + 3) != null, "corrected message can be processed");
    store.enqueue(new JSONArray().put(source(4, "周四开会")));
    check(
        store.queueStats().optInt("pending") == 1,
        "reverted text receives a fresh source revision");
  }

  private void workAndDelivery() throws Exception {
    OrganizerStore store = fresh();
    long now = System.currentTimeMillis(), due = now + 86400000L, remind = now + 60000L;
    JSONObject proposal = stage(store, 10, "提交资料", "todo", "create", null, due);
    String id = store.confirm(proposal, edited("提交资料", due), remind, now);
    JSONObject work = store.work(id);
    check(
        work.getInt("revision") == 1 && work.getString("state").equals("open"),
        "confirmed task created");
    check(
        store.confirm(proposal, edited("重复点击", due), remind, now).equals(id)
            && store.workItems(false).length() == 1,
        "confirmation idempotency");
    check(store.changes(id).length() == 1, "single provenance revision");
    check(!store.beginDelivery(id, 1, remind, now, true), "reminder never fires early");
    JSONObject change = stage(store, 11, "资料改到后天提交", "todo", "update", work, due + 86400000L);
    store.changeState(id, 1, "waiting", now + 1);
    boolean refused = false;
    try {
      store.confirm(change, edited("后天提交", due + 86400000L), remind, now + 2);
    } catch (Exception expected) {
      refused = true;
    }
    check(refused, "stale proposal cannot overwrite user edit");
    check(store.work(id).getString("state").equals("waiting"), "user state preserved");
    check(!store.beginDelivery(id, 1, remind, remind + 1, true), "old reminder revision fenced");
    work = store.work(id);
    change = stage(store, 12, "后天提交", "todo", "update", work, due + 86400000L);
    store.confirm(change, edited("后天提交", due + 86400000L), remind + 5000L, now + 3);
    work = store.work(id);
    check(work.getInt("revision") == 3, "reschedule increments same task");
    check(store.reminder(id).getLong("due_at") == remind + 5000L, "old reminder replaced");
    check(
        store.beginDelivery(id, 3, remind + 5000L, remind + 5001L, true),
        "due occurrence accepted once");
    store.finishDelivery(id, 3, remind + 5000L, remind + 5002L);
    check(
        store.acknowledge(id, 3, remind + 5000L, "dismiss", remind + 5003L),
        "dismiss acknowledgement");
    check(store.work(id).getString("state").equals("open"), "dismiss is not task completion");
    check(
        !store.acknowledge(id, 3, remind + 5000L, "snooze", remind + 5004L),
        "late duplicate action rejected");
    change = stage(store, 13, "取消提交", "todo", "cancel", store.work(id), null);
    store.confirm(change, edited("取消提交", due + 86400000L), null, now + 4);
    check(store.work(id).getString("state").equals("cancelled"), "evidence-backed cancellation");
    check(store.reminder(id).getString("state").equals("cancelled"), "cancel retracts reminder");
    check(
        !store.beginDelivery(id, 3, remind + 5000L, remind + 5005L, true),
        "cancelled task cannot ring");
    check(store.changes(id).length() == 4, "revision evidence retained");
    JSONObject another = stage(store, 14, "等回复", "todo", "create", null, due);
    String second = store.confirm(another, edited("等回复", due), remind, now);
    check(store.beginDelivery(second, 1, remind, remind + 1L, true), "second delivery");
    store.finishDelivery(second, 1, remind, remind + 2L);
    check(
        store.acknowledge(second, 1, remind, "snooze", remind + 3L),
        "snooze creates next occurrence");
    check(
        store.reminder(second).getLong("due_at") == remind + 3L + 300000L,
        "snooze keeps five-minute delay");
    check(
        !store.acknowledge(second, 1, remind, "complete", remind + 4L),
        "old notification cannot complete snoozed occurrence");
    long next = store.reminder(second).getLong("due_at");
    check(store.beginDelivery(second, 1, next, next + 1L, true), "snoozed occurrence fires once");
    store.finishDelivery(second, 1, next, next + 2L);
    check(store.acknowledge(second, 1, next, "complete", next + 3L), "explicit complete action");
    check(
        store.work(second).getString("state").equals("completed"),
        "complete action changes work state");
    JSONObject blocked = stage(store, 15, "通知权限验证", "todo", "create", null, due);
    String third = store.confirm(blocked, edited("通知权限验证", due), remind, now);
    check(
        !store.beginDelivery(third, 1, remind, remind + 1L, false)
            && store.reminder(third).getString("state").equals("blocked"),
        "denied notification is not delivered");
    store.retryBlocked(remind + 2L);
    check(store.beginDelivery(third, 1, remind, remind + 3L, true), "permission recovery");
    store.uncertainDelivery(third, 1, remind, remind + 4L);
    check(
        store.reminder(third).getString("state").equals("uncertain"),
        "uncertain presentation never claims success");
  }

  private void concurrentClaims() throws Exception {
    OrganizerStore store = fresh();
    store.enqueue(new JSONArray().put(source(20, "并发处理测试")));
    CountDownLatch start = new CountDownLatch(1), done = new CountDownLatch(2);
    AtomicInteger claims = new AtomicInteger(), errors = new AtomicInteger();
    for (int i = 0; i < 2; i++)
      new Thread(
              () -> {
                try (MemoryStore helper = new MemoryStore(context, NAME)) {
                  start.await();
                  if (new OrganizerStore(helper).claim(System.currentTimeMillis()) != null)
                    claims.incrementAndGet();
                } catch (Throwable error) {
                  errors.incrementAndGet();
                } finally {
                  done.countDown();
                }
              })
          .start();
    start.countDown();
    check(done.await(10, TimeUnit.SECONDS), "concurrency terminates");
    check(claims.get() == 1 && errors.get() == 0, "exactly one lease across database connections");
  }
}
