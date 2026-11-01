package com.mubai.infoinbox;

import static org.junit.Assert.*;

import android.app.Application;
import android.content.Context;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 33, application = Application.class)
public class KnowledgeStoreTest {
  private Context context;
  private MemoryStore memory;
  private KnowledgeStore store;
  private String name;

  @Before
  public void setup() throws Exception {
    context = RuntimeEnvironment.getApplication();
    name = "knowledge-test-" + UUID.randomUUID() + ".db";
    memory = new MemoryStore(context, name);
    store = new KnowledgeStore(memory, "https://fixture.invalid");
  }

  @After
  public void cleanup() {
    memory.close();
    context.deleteDatabase(name);
  }

  private void readyRuntimeConnection(boolean timely) {
    context.getSharedPreferences("knowledge", Context.MODE_PRIVATE).edit()
        .putBoolean("enabled", true).commit();
    context.getSharedPreferences("backend", Context.MODE_PRIVATE).edit()
        .putString("base", "https://fixture.invalid").putBoolean("configured", true)
        .putString("model", "runtime-connection").putBoolean("connected", true)
        .putString("owner_session", UUID.randomUUID().toString()).commit();
    context.getSharedPreferences("runtime-processing", Context.MODE_PRIVATE).edit()
        .putString("https://fixture.invalid", "runtime")
        .putBoolean("message_connection", timely).commit();
  }

  @Test
  public void cachedLocalCaptureStillPropagatesExclusionAndReinclusion() throws Exception {
    JSONObject room = source("合成范围变更");
    store.registerRoom(room);
    store.captureLocalState();
    store.captureLocalState();
    assertEquals(0, store.policies().length());
    ConversationPolicy policy = new ConversationPolicy(memory);
    policy.set(room, true);
    store.captureLocalState();
    assertEquals(1, store.policies().length());
    assertTrue(store.policies().getJSONObject(0).getBoolean("excluded"));
    assertTrue(store.excluded(KnowledgeWire.room(room)));
    policy.set(room, false);
    store.captureLocalState();
    assertEquals(1, store.policies().length());
    assertFalse(store.policies().getJSONObject(0).getBoolean("excluded"));
    assertFalse(store.excluded(KnowledgeWire.room(room)));
  }

  @Test
  public void collectedRuntimeSourceUsesTimelyConnectionWithoutLegacyIntake() {
    readyRuntimeConnection(true);
    new CollectorReceiver().onReceive(context,
        new android.content.Intent("com.mubai.infoinbox.COLLECTED"));
    android.content.Intent started = org.robolectric.Shadows.shadowOf((Application) context)
        .getNextStartedService();
    assertNotNull(started);
    assertEquals(RuntimeMessageService.class.getName(), started.getComponent().getClassName());
    assertEquals("sync", started.getAction());
    android.app.job.JobScheduler jobs=context.getSystemService(android.app.job.JobScheduler.class);
    assertNull(jobs.getPendingJob(8803));
    assertNull(jobs.getPendingJob(8811));
    assertTrue(context.getSharedPreferences("collector-events", Context.MODE_PRIVATE)
        .getLong("dirty_at", 0) > 0);
  }

  @Test
  public void disabledTimelyConnectionKeepsDurableSyncFallbackWithoutLocalAiIntake() {
    readyRuntimeConnection(false);
    new CollectorReceiver().onReceive(context,
        new android.content.Intent("com.mubai.infoinbox.COLLECTED"));
    android.app.job.JobScheduler jobs=context.getSystemService(android.app.job.JobScheduler.class);
    assertNotNull(jobs.getPendingJob(8811));
    assertNull(jobs.getPendingJob(8803));
  }

  @Test
  public void localEditsAndControlOutboxWakeTheTimelyConnection() throws Exception {
    readyRuntimeConnection(true);
    try (MemoryStore main = new MemoryStore(context)) {
      main.save(source("本地确认的合成事实"), "合成记忆", "fact");
      android.content.Intent started=org.robolectric.Shadows.shadowOf((Application) context)
          .getNextStartedService();
      assertNotNull(started);
      assertEquals("sync", started.getAction());
      assertEquals(RuntimeMessageService.class.getName(), started.getComponent().getClassName());
      assertNull(context.getSystemService(android.app.job.JobScheduler.class).getPendingJob(8811));
      KnowledgeStore mainStore = new KnowledgeStore(main, "https://fixture.invalid");
      RuntimeActions.enqueue(mainStore, "/v1/agent/intents/"+UUID.randomUUID(),
          new JSONObject().put("expected_revision", 1).put("action", "pause"), "合成暂停");
      assertEquals("sync", org.robolectric.Shadows.shadowOf((Application) context)
          .getNextStartedService().getAction());
      assertEquals(1, RuntimeActions.list(mainStore).length());
    } finally {
      context.deleteDatabase("information.db");
    }
  }

  @Test
  public void rejectedTimelyServiceStartRetainsJobAndSkipsOldAiQueue() throws Exception {
    readyRuntimeConnection(true);
    Context restricted = new android.content.ContextWrapper(context) {
      @Override public android.content.ComponentName startForegroundService(android.content.Intent intent) {
        throw new IllegalStateException("background_service_restricted");
      }
    };
    new CollectorReceiver().onReceive(restricted,
        new android.content.Intent("com.mubai.infoinbox.COLLECTED"));
    android.app.job.JobScheduler jobs=context.getSystemService(android.app.job.JobScheduler.class);
    assertNotNull(jobs.getPendingJob(8811));
    assertNull(jobs.getPendingJob(8803));
    assertEquals(0, AnalysisEngine.intake(restricted, true));
  }

  @Test
  public void oneSystemWakeDrainsAllDueRulesWithoutDuplicateDelivery() throws Exception {
    org.robolectric.Shadows.shadowOf((Application) context)
        .grantPermissions(android.Manifest.permission.POST_NOTIFICATIONS);
    android.app.NotificationManager notices =
        context.getSystemService(android.app.NotificationManager.class);
    try (MemoryStore main = new MemoryStore(context)) {
      OrganizerStore organizer = new OrganizerStore(main);
      long due = System.currentTimeMillis() - 1000;
      String[] ids = new String[3];
      for (int i = 0; i < ids.length; i++) {
        JSONObject original = source("合成到期提醒").put("msg_id", 700 + i);
        JSONObject proposal = organizer.manual(original, "todo", "到期事项 " + i);
        JSONObject edited = new JSONObject().put("title", "到期事项 " + i)
            .put("state", "open").put("involvement", "self")
            .put("timezone", "Asia/Shanghai").put("due_at", due)
            .put("starts_at", JSONObject.NULL).put("ends_at", JSONObject.NULL);
        ids[i] = organizer.confirm(proposal, edited, due, due - 1000);
      }
      ReminderScheduler.onAlarm(context, ids[0], 1, due);
      for (String id : ids) assertEquals("posted", organizer.reminder(id).getString("state"));
      assertEquals(3, org.robolectric.Shadows.shadowOf(notices).getAllNotifications().size());
      ReminderScheduler.onAlarm(context, ids[0], 1, due);
      assertEquals(3, org.robolectric.Shadows.shadowOf(notices).getAllNotifications().size());
      try (android.database.Cursor rows = main.getReadableDatabase()
          .rawQuery("SELECT count(*) FROM delivery_history", null)) {
        assertTrue(rows.moveToFirst());
        assertEquals(3, rows.getInt(0));
      }
    } finally {
      notices.cancelAll();
      context.deleteDatabase("information.db");
    }
  }

  @Test
  public void schemaFourUpgradePreservesExistingConfirmedMemory() throws Exception {
    memory.save(source("历史原文"), "已有的长期记忆", "fact");
    for (String table :
        new String[] {
          "knowledge_meta",
          "knowledge_shadow",
          "knowledge_outbox",
          "knowledge_sources",
          "knowledge_rooms",
          "knowledge_source_refs",
          "knowledge_priority",
          "knowledge_ack_outbox"
        }) {
      memory.getWritableDatabase().execSQL("DROP TABLE " + table);
    }
    memory.getWritableDatabase().setVersion(4);
    memory.close();
    memory = new MemoryStore(context, name);
    store = new KnowledgeStore(memory, "https://fixture.invalid");
    assertEquals(6, memory.getReadableDatabase().getVersion());
    assertEquals("已有的长期记忆", memory.list().getJSONObject(0).getString("title"));
    assertEquals(0, store.mutations().length());
  }

  @Test
  public void runtimeDecisionsKeepStableReceiptsInAnOfflineOutbox() throws Exception {
    JSONObject body=new JSONObject().put("session_id","fixture").put("decision","keep_server").put("expected_revision",7);
    RuntimeActions.enqueue(store,"/v1/agent/review/sync/conflict",body,"保留服务端版本");
    JSONObject saved=RuntimeActions.list(store).getJSONObject(0);
    assertEquals(saved.getString("operation"),saved.getJSONObject("payload").getString("operation_id"));
    assertEquals(7,saved.getJSONObject("payload").getInt("expected_revision"));
    memory.close();memory=new MemoryStore(context,name);store=new KnowledgeStore(memory,"https://fixture.invalid");
    assertEquals(saved.getString("operation"),RuntimeActions.list(store).getJSONObject(0).getString("operation"));
    RuntimeActions.discard(store,saved.getString("operation"));assertEquals(0,RuntimeActions.list(store).length());
  }

  @Test
  public void externalMatterEditsUseRuntimeActionsInsteadOfOverwritingKnowledge() throws Exception {
    JSONObject record=KnowledgeWire.draft("fixture:external","todo","manual",1791800000000L).put("title","原待办").put("confirmed",true).put("state","open").put("revision",1).put("deleted",false).put("updated_at",1791800000000L)
        .put("origin",new JSONObject().put("source_type","agent_harness").put("work_item_id","work:external").put("work_revision",1).put("external_bound",true));
    store.applyRecord(record,false);
    String local;
    try(android.database.Cursor row=store.db.rawQuery("SELECT local_id FROM knowledge_shadow WHERE space=? AND id=?",new String[]{store.space,"fixture:external"})){assertTrue(row.moveToFirst());local=row.getString(0);}
    store.db.execSQL("UPDATE work_items SET title='修改后的待办',revision=revision+1 WHERE id=?",new Object[]{local});
    JSONObject draft=store.localRecord(local,"todo");store.queue(draft,local,"upsert");store.queue(draft,local,"upsert");
    assertEquals(0,store.mutations().length());
    JSONArray actions=RuntimeActions.list(store);assertEquals(1,actions.length());
    assertEquals(1,actions.getJSONObject(0).getJSONObject("payload").getInt("expected_revision"));
    assertEquals("修改后的待办",actions.getJSONObject(0).getJSONObject("payload").getJSONObject("changes").getString("title"));
  }

  private JSONObject source(String text) throws Exception {
    return new JSONObject()
        .put("account_id", "test-owner")
        .put("msg_id", 1)
        .put("talker", "test-person")
        .put("content", text)
        .put("created_at", 1791800000000L)
        .put("is_send", 0)
        .put("conversation_title", "合成会话");
  }

  private JSONObject received(JSONObject draft, int revision) throws Exception {
    return new JSONObject(draft.toString())
        .put("revision", revision)
        .put("deleted", false)
        .put("updated_at", 1791800000000L + revision);
  }

  private void accept(JSONObject operation, int revision) throws Exception {
    store.receiveReceipt(
        new JSONObject()
            .put("operation_id", operation.getString("operation_id"))
            .put("record_id", operation.getJSONObject("record").getString("record_id"))
            .put("status", "accepted")
            .put("record", received(operation.getJSONObject("record"), revision)));
  }

  private JSONObject pull(String kind, JSONObject value, int cursor) throws Exception {
    return new JSONObject()
        .put("policies", new JSONArray())
        .put("policy_epoch", 0)
        .put("cursor", cursor)
        .put(
            "changes", new JSONArray().put(new JSONObject().put("kind", kind).put("value", value)));
  }

  @Test
  public void immutableOperationSurvivesDatabaseReopen() throws Exception {
    store.intake(new JSONArray().put(source("周四开会")));
    JSONObject first = store.mutations().getJSONObject(0);
    memory.close();
    memory = new MemoryStore(context, name);
    store = new KnowledgeStore(memory, "https://fixture.invalid");
    assertEquals(
        KnowledgeWire.canonical(first),
        KnowledgeWire.canonical(store.mutations().getJSONObject(0)));
    accept(first, 1);
    assertEquals(0, store.mutations().length());
    store.intake(new JSONArray().put(source("周四开会")));
    assertEquals(0, store.mutations().length());
  }

  @Test
  public void changedSourceWaitsForOldReceiptWithoutLosingNewText() throws Exception {
    store.intake(new JSONArray().put(source("周四开会")));
    JSONObject old = store.mutations().getJSONObject(0);
    store.intake(new JSONArray().put(source("改到周五")));
    assertEquals(
        old.getString("operation_id"),
        store.mutations().getJSONObject(0).getString("operation_id"));
    accept(old, 1);
    store.intake(new JSONArray().put(source("改到周五")));
    JSONObject next = store.mutations().getJSONObject(0);
    assertEquals(1, next.getInt("expected_revision"));
    assertEquals("改到周五", next.getJSONObject("record").getString("text"));
  }

  @Test
  public void remoteWorkRescheduleAndCancelUpdateNativeReminderRevision() throws Exception {
    JSONObject draft =
        KnowledgeWire.draft("work:fixture", "todo", "fixture:web-origin", 1791800000000L)
            .put("origin", new JSONObject().put("source_type", "web_manual"))
            .put("title", "项目评审")
            .put("confirmed", true)
            .put("state", "open")
            .put("remind_at", 1791900000000L);
    store.applyPull(pull("record", received(draft, 1), 1));
    store.captureConfirmed();
    assertEquals("First remote creation must not echo", 0, store.mutations().length());
    JSONObject initial = new OrganizerStore(memory).reminder("work:fixture");
    assertEquals(1791900000000L, initial.getLong("due_at"));
    draft.put("remind_at", 1792000000000L);
    store.applyPull(pull("record", received(draft, 2), 2));
    store.captureConfirmed();
    assertEquals("Remote reschedule must not echo", 0, store.mutations().length());
    JSONObject later = new OrganizerStore(memory).reminder("work:fixture");
    assertEquals(1792000000000L, later.getLong("due_at"));
    assertTrue(later.getInt("revision") > initial.getInt("revision"));
    draft.put("state", "cancelled");
    store.applyPull(pull("record", received(draft, 3), 3));
    assertEquals(
        "cancelled", new OrganizerStore(memory).reminder("work:fixture").getString("state"));
    store.captureConfirmed();
    assertEquals("Remote application must not echo into outbox", 0, store.mutations().length());
  }

  @Test
  public void offlineEditIsPreservedUntilExplicitConflictResolution() throws Exception {
    store.intake(new JSONArray().put(source("原始约定")));
    accept(store.mutations().getJSONObject(0), 1);
    memory.save(source("原始约定"), "本机事项信息", "fact");
    store.captureConfirmed();
    JSONObject first = store.mutations().getJSONObject(0);
    accept(first, 1);
    memory.save(source("原始约定"), "手机离线修改", "fact");
    store.captureConfirmed();
    JSONObject offline = store.mutations().getJSONObject(0),
        remote = received(first.getJSONObject("record"), 2).put("title", "网页修改");
    store.applyPull(pull("record", remote, 1));
    assertEquals("手机离线修改", memory.list().getJSONObject(0).getString("title"));
    String conflict = UUID.randomUUID().toString();
    store.receiveReceipt(
        new JSONObject()
            .put("record_id", remote.getString("record_id"))
            .put("operation_id", offline.getString("operation_id"))
            .put("status", "conflict")
            .put("conflict_id", conflict));
    assertEquals(0, store.mutations().length());
    store.applyPull(
        pull(
            "conflict_resolved",
            new JSONObject().put("record", remote).put("conflict_id", conflict),
            2));
    assertEquals("网页修改", memory.list().getJSONObject(0).getString("title"));
    store.captureConfirmed();
    assertEquals(0, store.mutations().length());
  }

  @Test
  public void echoedExclusionsPreserveNamesAndOriginalTime() throws Exception {
    JSONObject room = source("原文");
    ConversationPolicy policy = new ConversationPolicy(memory);
    policy.set(room, true);
    memory.getWritableDatabase().execSQL("UPDATE conversation_exclusions SET created_at=123");
    String roomId = store.registerRoom(room);
    JSONObject pending = store.policies().getJSONObject(0);
    store.receiveReceipt(
        new JSONObject()
            .put("operation_id", pending.getString("operation_id"))
            .put("room_id", roomId)
            .put("excluded", true)
            .put("status", "accepted")
            .put("revision", 1));
    JSONObject remote =
        new JSONObject()
            .put("room_id", roomId)
            .put("revision", 1)
            .put("excluded", true)
            .put("label", "服务器名称");
    JSONObject batch =
        new JSONObject()
            .put("policy_epoch", 1)
            .put("cursor", 1)
            .put("changes", new JSONArray())
            .put("policies", new JSONArray().put(remote));
    store.applyPull(batch);
    assertEquals("合成会话", policy.list().getJSONObject(0).getString("title"));
    try (android.database.Cursor c =
        memory
            .getReadableDatabase()
            .rawQuery("SELECT created_at FROM conversation_exclusions", null)) {
      assertTrue(c.moveToFirst());
      assertEquals(123, c.getLong(0));
    }
    remote.put("revision", 2).put("excluded", false);
    store.applyPull(batch);
    assertFalse(policy.excluded(room));
    remote.put("revision", 3).put("excluded", true);
    store.applyPull(batch);
    assertTrue(policy.excluded(room));
    assertEquals("服务器名称", policy.list().getJSONObject(0).getString("title"));
  }

  @Test
  public void localExclusionImmediatelyStopsPendingSourceUpload() throws Exception {
    JSONObject input = source("应保留在本机");
    store.intake(new JSONArray().put(input));
    assertEquals(1, store.mutations().length());
    new ConversationPolicy(memory).set(input, true);
    assertEquals(0, store.mutations().length());
    assertEquals(1, store.policies().length());
    assertEquals(
        "应保留在本机",
        store
            .localSource(KnowledgeWire.sourceRecords(input).getJSONObject(0).getString("record_id"))
            .getString("content"));
  }

  @Test
  public void oldUploadsWaitForWifiButConfirmedEvidenceCanProceed() throws Exception {
    JSONObject input =
        source("历史原文").put("created_at", System.currentTimeMillis() - 60L * 86400000L);
    store.intake(new JSONArray().put(input));
    JSONObject pending = store.mutations().getJSONObject(0);
    assertEquals(0, store.mutations(false).length());
    memory
        .getWritableDatabase()
        .execSQL(
            "INSERT INTO knowledge_priority VALUES(?,?,?)",
            new Object[] {store.space, "test-owner", 1});
    assertEquals(
        pending.getString("operation_id"),
        store.mutations(false).getJSONObject(0).getString("operation_id"));
  }

  @Test
  public void ordinaryClaimDoesNotConsumePausedHistory() throws Exception {
    OrganizerStore organizer = new OrganizerStore(memory);
    long now = System.currentTimeMillis();
    organizer.enqueue(
        new JSONArray()
            .put(
                source("十年前的原文")
                    .put("type", 1)
                    .put("is_send", 0)
                    .put("created_at", now - 3650L * 86400000L)));
    assertNull(organizer.claim(now, now - 7L * 86400000L));
    JSONObject full = organizer.claim(now, 0);
    assertNotNull(full);
    assertEquals("十年前的原文", full.getJSONArray("sources").getJSONObject(0).getString("content"));
  }

  @Test
  public void analysisBatchKeepsOneConversationAndContinuousTimeWindow() throws Exception {
    long now = 1791800000000L;
    OrganizerStore organizer = new OrganizerStore(memory);
    JSONObject old =
        source("很早的另一个话题").put("type", 1).put("msg_id", 1).put("created_at", now - 7200000);
    JSONObject other =
        source("另一个会话")
            .put("type", 1)
            .put("msg_id", 2)
            .put("talker", "other-room")
            .put("created_at", now - 100);
    JSONObject current = source("这次的当前对话").put("type", 1).put("msg_id", 3);
    organizer.enqueue(new JSONArray().put(old).put(other).put(current));
    JSONArray batch = organizer.claim(now).getJSONArray("sources");
    assertEquals(1, batch.length());
    assertEquals(3, batch.getJSONObject(0).getInt("msg_id"));
  }

  @Test
  public void completeMemorySummarySyncsWithoutReplacingOriginalEvidence() throws Exception {
    String summary = "本人原定周五提交，后与对方明确改为下周一；这是暂定安排，尚待最终确认。";
    JSONObject raw = source("改到下周一吧").put("_information_summary", summary);
    memory.save(raw, "提交安排", "记忆");
    JSONObject saved = memory.list().getJSONObject(0);
    assertEquals(summary, saved.getString("summary"));
    assertEquals("改到下周一吧", saved.getJSONObject("source").getString("content"));
    assertEquals(summary, store.localRecord(saved.getString("ref"), "memory").getString("text"));
  }

  @Test
  public void contextWindowKeepsOldProgressAndRetainsBothSidesAsEvidence() throws Exception {
    long now = 1791800000000L;
    OrganizerStore organizer = new OrganizerStore(memory);
    JSONObject old = source("周五交").put("type", 1).put("msg_id", 1).put("created_at", now - 2000);
    JSONObject current =
        source("改成周一").put("type", 1).put("msg_id", 2).put("created_at", now - 1000);
    JSONObject later = source("先取消吧").put("type", 1).put("msg_id", 3).put("created_at", now);
    organizer.enqueue(new JSONArray().put(old).put(current));
    memory
        .getWritableDatabase()
        .execSQL(
            "UPDATE queued_sources SET state='done' WHERE base_ref=?",
            new Object[] {MemoryStore.reference(old)});
    JSONObject batch = organizer.claim(now, 0);
    organizer.expandContext(batch, new JSONArray().put(later).put(old).put(current), now, 0);
    AnalysisPayload input =
        AnalysisPayload.prepare(
            batch.getJSONArray("sources"), new JSONArray(), new JSONArray(), "Asia/Shanghai");
    assertEquals(3, input.allowed.size());
    assertEquals(2, input.references().length());
    JSONArray refs = new JSONArray();
    for (String ref : input.allowed.keySet()) refs.put(ref);
    JSONObject suggestion =
        new JSONObject()
            .put("title", "提交安排已取消")
            .put("kind", "reference")
            .put("importance", "medium")
            .put("involvement", "self")
            .put("reason", "后文明确取消")
            .put("details", "原计划周五，随后改为周一，最终明确取消，当前无需提交。")
            .put("source_refs", refs);
    JSONObject result = input.validate(new JSONArray().put(suggestion));
    organizer.finish(batch, result, now);
    assertEquals(3, organizer.queueStats().getInt("done"));
    JSONArray evidence =
        memory.suggestions().getJSONObject(0).getJSONObject("source").getJSONArray("evidence");
    assertEquals("周五交", evidence.getJSONObject(0).getString("content"));
    assertEquals("先取消吧", evidence.getJSONObject(2).getString("content"));
    JSONObject savedProposal = memory.suggestions().getJSONObject(0);
    organizer.confirmMemory(savedProposal, "提交安排已取消", "资料");
    JSONObject saved = memory.list().getJSONObject(0);
    assertEquals(suggestion.getString("details"), saved.getString("summary"));
    assertNull(store.localRecord(saved.getString("ref"), "memory"));
    store.intake(new JSONArray().put(old).put(current).put(later));
    JSONArray sourceMutations = store.mutations();
    for (int i = 0; i < sourceMutations.length(); i++) accept(sourceMutations.getJSONObject(i), 1);
    JSONObject record = store.localRecord(saved.getString("ref"), "memory");
    assertEquals(suggestion.getString("details"), record.getString("text"));
    assertEquals(3, record.getJSONArray("source_refs").length());
  }

  @Test
  public void changedContextFencesSendingAndFailureKeepsOldMessagesDone() throws Exception {
    long now = 1791800000000L;
    OrganizerStore organizer = new OrganizerStore(memory);
    JSONObject old = source("原约定").put("type", 1).put("msg_id", 1).put("created_at", now - 1000);
    JSONObject current = source("新的回复").put("type", 1).put("msg_id", 2);
    organizer.enqueue(new JSONArray().put(old).put(current));
    memory
        .getWritableDatabase()
        .execSQL(
            "UPDATE queued_sources SET state='done' WHERE base_ref=?",
            new Object[] {MemoryStore.reference(old)});
    JSONObject batch = organizer.claim(now, 0);
    organizer.expandContext(batch, new JSONArray().put(old).put(current), now, 0);
    organizer.fail(batch.getString("id"), now, false);
    assertEquals(1, organizer.queueStats().getInt("done"));
    JSONObject retry = organizer.claim(now + 61000, 0);
    organizer.expandContext(retry, new JSONArray().put(old).put(current), now + 61000, 0);
    organizer.enqueue(new JSONArray().put(new JSONObject(old.toString()).put("content", "原文已修正")));
    try {
      organizer.requireActiveBatch(retry, now + 61000);
      fail("Changed context must invalidate the run");
    } catch (IllegalStateException expected) {
      assertTrue(expected.getMessage().contains("变化"));
    }
  }

  @Test
  public void forgottenNeighborNeverEntersTheContextWindow() throws Exception {
    long now = 1791800000000L;
    OrganizerStore organizer = new OrganizerStore(memory);
    JSONObject current = source("当前消息").put("type", 1).put("msg_id", 1);
    JSONObject forgotten = source("已遗忘的相邻消息").put("type", 1).put("msg_id", 2);
    memory
        .getWritableDatabase()
        .execSQL(
            "INSERT INTO knowledge_shadow(space,id,revision,hash,payload,local_id)"
                + " VALUES(?,?,1,'','{\"deleted\":true}','')",
            new Object[] {"fixture", MemoryStore.reference(forgotten) + ":part:0"});
    organizer.enqueue(new JSONArray().put(current));
    JSONObject batch = organizer.claim(now, 0);
    organizer.expandContext(batch, new JSONArray().put(forgotten), now, 0);
    assertEquals(1, batch.getJSONArray("sources").length());
  }

  @Test
  public void pendingNewContextDoesNotForgetAnExistingSyncedMemory() throws Exception {
    JSONObject original = source("原约定");
    memory.save(original, "原结论", "记忆");
    store.intake(new JSONArray().put(original));
    accept(store.mutations().getJSONObject(0), 1);
    store.captureConfirmed();
    accept(store.mutations().getJSONObject(0), 1);
    JSONObject later = source("后续修正").put("msg_id", 2);
    JSONObject updated =
        new JSONObject(original.toString())
            .put("_analysis_context_version", "1.2")
            .put("_information_summary", "原约定已有明确修正，当前仍需核对。")
            .put("evidence", new JSONArray().put(original).put(later));
    memory.save(updated, "修正后的结论", "记忆");
    store.captureConfirmed();
    assertEquals(0, store.mutations().length());
    assertEquals(1, memory.list().length());
    store.intake(new JSONArray().put(later));
    accept(store.mutations().getJSONObject(0), 1);
    store.captureConfirmed();
    JSONObject operation = store.mutations().getJSONObject(0);
    assertEquals("upsert", operation.getString("action"));
    assertEquals(2, operation.getJSONObject("record").getJSONArray("source_refs").length());
  }

  @Test
  public void importedMemoryKeepsFullTextWhenItsOriginalIsNotOnThisPhone() throws Exception {
    JSONObject record =
        received(
            KnowledgeWire.draft("memory:remote-fixture", "memory", "remote-room", 1791800000000L)
                .put("title", "跨端确认内容")
                .put("text", "网页已确认的完整结论，包含条件与未确定事项。")
                .put("confirmed", true),
            1);
    store.applyRecord(record, false);
    JSONObject local = memory.list().getJSONObject(0);
    assertEquals(record.getString("text"), local.getString("summary"));
    assertEquals(
        record.getString("record_id"), local.getJSONObject("source").getString("_knowledge_id"));
    assertEquals(
        record.getString("text"),
        store.localRecord(local.getString("ref"), "memory").getString("text"));
    memory.save(
        local.getJSONObject("source").put("_information_summary", "跨端记忆在本机核对后的完整结论。"),
        "本机核对标题",
        "记忆");
    assertEquals(1, memory.list().length());
    assertEquals(
        "跨端记忆在本机核对后的完整结论。", store.localRecord(local.getString("ref"), "memory").getString("text"));
  }

  @Test
  public void analysisParallelismIsBoundedAndSameRoomCannotOvertake() throws Exception {
    OrganizerStore organizer = new OrganizerStore(memory);
    long now = 1791800000000L;
    JSONArray rows = new JSONArray();
    for (int i = 0; i < 5; i++)
      rows.put(
          source("合成历史 " + i)
              .put("type", 1)
              .put("msg_id", 100 + i)
              .put("talker", "room-" + i)
              .put("created_at", now + i));
    organizer.enqueue(rows);
    java.util.Set<String> rooms = new java.util.HashSet<>();
    for (int i = 0; i < 4; i++) {
      JSONObject batch = organizer.claim(now + 100, 0, true);
      assertNotNull(batch);
      rooms.add(batch.getJSONArray("sources").getJSONObject(0).getString("talker"));
    }
    assertEquals(4, rooms.size());
    assertNull(organizer.claim(now + 100, 0, true));
  }

  @Test
  public void chronologicalRetryBlocksLaterMessagesOnlyInItsOwnRoom() throws Exception {
    OrganizerStore organizer = new OrganizerStore(memory);
    long now = 1791800000000L;
    organizer.enqueue(
        new JSONArray()
            .put(source("早期前提").put("type", 1).put("created_at", now).put("msg_id", 101))
            .put(
                source("一小时后的修正")
                    .put("type", 1)
                    .put("created_at", now + 3600000)
                    .put("msg_id", 102))
            .put(
                source("另一会话")
                    .put("type", 1)
                    .put("created_at", now + 1)
                    .put("msg_id", 103)
                    .put("talker", "other-room")));
    JSONObject first = organizer.claim(now + 4000000, 0, true);
    assertEquals(101, first.getJSONArray("sources").getJSONObject(0).getLong("msg_id"));
    JSONObject other = organizer.claim(now + 4000000, 0, true);
    assertEquals("other-room", other.getJSONArray("sources").getJSONObject(0).getString("talker"));
    assertNull(organizer.claim(now + 4000000, 0, true));
    organizer.fail(first.getString("id"), now + 4000000, false);
    assertNull(organizer.claim(now + 4001000, 0, true));
    JSONObject retry = organizer.claim(now + 4061000, 0, true);
    assertEquals(101, retry.getJSONArray("sources").getJSONObject(0).getLong("msg_id"));
  }

  @Test
  public void earlierKnowledgeKeepsReviewStateScopeAndInvalidatesAfterEdit() throws Exception {
    OrganizerStore organizer = new OrganizerStore(memory);
    JSONObject earlier =
        source("旧原文")
            .put("msg_id", 201)
            .put("created_at", 1000)
            .put("_information_summary", "已确认的旧结论");
    memory.save(earlier, "已确认安排", "记忆");
    memory.save(source("未来原文").put("msg_id", 202).put("created_at", 10000), "未来信息", "记忆");
    memory.save(
        source("其他会话").put("talker", "other-room").put("msg_id", 203).put("created_at", 1000),
        "不能跨会话使用",
        "记忆");
    JSONObject proposed =
        new JSONObject()
            .put("review_ref", "ai:" + AnalysisContract.hash("earlier-summary"))
            .put("title", "待核对安排")
            .put("kind", "memory")
            .put("importance", "low")
            .put("reason", "待核对")
            .put("details", "旧讨论的猜测仍待核对")
            .put("source", source("旧讨论").put("msg_id", 204).put("created_at", 2000));
    android.content.ContentValues values = new android.content.ContentValues();
    values.put("ref", proposed.getString("review_ref"));
    values.put("title", "待核对安排");
    values.put("kind", "memory");
    values.put("importance", "low");
    values.put("reason", "待核对");
    values.put("item_json", proposed.toString());
    values.put("created_at", 2000);
    values.put("state", "pending");
    memory.getWritableDatabase().insertOrThrow("ai_reviews", null, values);
    organizer.enqueue(
        new JSONArray()
            .put(source("后续消息").put("type", 1).put("msg_id", 205).put("created_at", 5000)));
    JSONObject batch = organizer.claim(6000, 0, true);
    JSONArray context = organizer.analysisContext(batch);
    assertEquals(2, context.length());
    assertEquals("confirmed", context.getJSONObject(0).getString("state"));
    assertEquals("pending_review", context.getJSONObject(1).getString("state"));
    AnalysisPayload prepared =
        AnalysisPayload.prepare(
            batch.getJSONArray("sources"), new JSONArray(), context, "Asia/Shanghai");
    JSONObject payload = prepared.input;
    assertEquals(2, payload.getJSONArray("prior_knowledge").length());
    assertFalse(payload.getJSONArray("prior_knowledge").getJSONObject(0).has("_context_source"));
    JSONObject answer =
        new JSONObject()
            .put("title", "后续新结论")
            .put("kind", "memory")
            .put("importance", "medium")
            .put("reason", "依照当前原文核对")
            .put("details", "完整的新结论，仍需核对。")
            .put("involvement", "unclear")
            .put("source_refs", new JSONArray().put(prepared.references().getString(0)));
    JSONObject validated =
        prepared.validate(new JSONArray().put(answer)).getJSONArray("items").getJSONObject(0);
    assertEquals(3, validated.getJSONObject("source").getJSONArray("evidence").length());
    assertEquals(2, validated.getJSONObject("source").getJSONArray("_prior_knowledge").length());
    organizer.requireActiveBatch(batch, 6000);
    String forgotten = MemoryStore.reference(earlier) + ":part:1";
    memory
        .getWritableDatabase()
        .execSQL(
            "INSERT INTO knowledge_outbox(space,id,action) VALUES(?,?,'forget')",
            new Object[] {store.space, forgotten});
    assertFalse(new ConversationPolicy(memory).allowsProposal(validated));
    memory
        .getWritableDatabase()
        .delete("knowledge_outbox", "space=? AND id=?", new String[] {store.space, forgotten});
    memory.save(earlier, "用户已经更正", "记忆");
    assertThrows(IllegalStateException.class, () -> organizer.requireActiveBatch(batch, 6000));
  }

  @Test
  public void backgroundWithTooManySourcesIsSkippedWithoutLosingProvenance() throws Exception {
    JSONArray evidence = new JSONArray();
    for (int i = 0; i < 30; i++)
      evidence.put(source("历史依据").put("msg_id", 300 + i).put("created_at", 1000 + i));
    memory.save(
        source("旧结论").put("msg_id", 300).put("created_at", 1000).put("evidence", evidence),
        "已确认历史结论",
        "记忆");
    OrganizerStore organizer = new OrganizerStore(memory);
    organizer.enqueue(
        new JSONArray()
            .put(source("新消息").put("type", 1).put("msg_id", 400).put("created_at", 5000)));
    JSONObject batch = organizer.claim(6000, 0, true);
    assertEquals(0, organizer.analysisContext(batch).length());
    assertTrue(batch.getJSONArray("sources").getJSONObject(0).getBoolean("_context_incomplete"));
  }

  @Test
  public void discoveringUnqueuedHistoryYieldsTheNewerLeaseWithoutAdvancingIt() throws Exception {
    OrganizerStore organizer = new OrganizerStore(memory);
    long now = 1791800000000L;
    organizer.enqueue(
        new JSONArray()
            .put(source("先入队的新消息").put("type", 1).put("msg_id", 501).put("created_at", now)));
    JSONObject newer = organizer.claim(now + 1, 0, true);
    organizer.yieldToEarlierSources(
        newer,
        new JSONArray()
            .put(
                source("补发现的早期前提")
                    .put("type", 1)
                    .put("msg_id", 500)
                    .put("created_at", now - 3600000)),
        now + 2);
    JSONObject earlier = organizer.claim(now + 3, 0, true);
    assertEquals(500, earlier.getJSONArray("sources").getJSONObject(0).getLong("msg_id"));
    assertEquals(0, organizer.queueStats().optInt("done"));
    assertThrows(IllegalStateException.class, () -> organizer.requireActiveBatch(newer, now + 3));
  }
}
