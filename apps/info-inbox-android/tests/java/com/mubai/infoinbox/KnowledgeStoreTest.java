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
    assertEquals(5, memory.getReadableDatabase().getVersion());
    assertEquals("已有的长期记忆", memory.list().getJSONObject(0).getString("title"));
    assertEquals(0, store.mutations().length());
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
}
