package com.mubai.infoinbox;

import static org.junit.Assert.*;

import java.util.LinkedHashMap;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class AnalysisContractTest {
  private JSONObject source() throws Exception {
    return new JSONObject()
        .put("account_id", "a")
        .put("talker", "room")
        .put("msg_id", 1)
        .put("content", "资料已发送")
        .put("created_at", 1791799200000L);
  }

  private Map<String, JSONObject> allowed() throws Exception {
    JSONObject source = source();
    Map<String, JSONObject> result = new LinkedHashMap<>();
    result.put(MemoryStore.reference(source), source);
    return result;
  }

  private JSONObject proposal() throws Exception {
    return new JSONObject()
        .put("title", "核对资料")
        .put("kind", "todo")
        .put("importance", "high")
        .put("reason", "明确的请求")
        .put("involvement", "self")
        .put("source_refs", new JSONArray().put(MemoryStore.reference(source())));
  }

  private Map<String, JSONObject> targets() throws Exception {
    Map<String, JSONObject> result = new LinkedHashMap<>();
    result.put(
        "work:1",
        new JSONObject()
            .put("id", "work:1")
            .put("account_id", "a")
            .put("talker", "room")
            .put("kind", "todo")
            .put("revision", 2));
    return result;
  }

  private JSONArray validate(JSONObject item, Map<String, JSONObject> targets) throws Exception {
    return AnalysisContract.validate(
        new JSONArray().put(item), allowed(), targets, "Asia/Shanghai");
  }

  @Test
  public void preservesUnknownDatesAndEvidence() throws Exception {
    JSONObject item = validate(proposal(), new LinkedHashMap<>()).getJSONObject(0);
    assertTrue(item.isNull("due_at"));
    assertEquals("create", item.getString("operation"));
    assertEquals(
        "资料已发送",
        item.getJSONObject("source")
            .getJSONArray("evidence")
            .getJSONObject(0)
            .getString("content"));
  }

  @Test
  public void processingFailuresHaveSafeDistinctReasons() {
    assertEquals("model_output_invalid", AnalysisEngine.failureCode(
        new RuntimeBackendClient.AnalysisFailure("model_output_invalid")));
    assertEquals("network_timeout", AnalysisEngine.failureCode(new java.net.SocketTimeoutException()));
    assertEquals("connection_failed", AnalysisEngine.failureCode(new java.io.IOException()));
    assertEquals("storage_busy", AnalysisEngine.failureCode(
        new android.database.sqlite.SQLiteDatabaseLockedException()));
    assertTrue(AnalysisEngine.failureMessage("model_output_invalid").contains("格式或出处"));
    assertTrue(AnalysisEngine.failureMessage("analysis_failed").contains("未记录具体原因"));
  }

  @Test
  public void oldCommitmentsNeedReviewButFuturePlansAndRecentOverdueWorkSurvive() throws Exception {
    long now = java.time.Instant.parse("2026-11-01T01:00:00Z").toEpochMilli();
    JSONObject item = proposal().put("source", source().put("created_at", 1717570800000L));
    assertEquals("uncertain", ProposalRelevance.assess(item, now));
    item.put("due_at", "2024-06-05T17:00:00+08:00");
    assertEquals("historical", ProposalRelevance.assess(item, now));
    item.remove("due_at");
    item.put("due_date", "2026-11-10");
    assertEquals("current", ProposalRelevance.assess(item, now));
    item.remove("due_date");
    item.put("due_at", "2026-10-29T17:00:00+08:00");
    item.getJSONObject("source").put("created_at", now - 86400000L);
    assertEquals("current", ProposalRelevance.assess(item, now));
    item.put("kind", "event").put("starts_at", "2026-10-30T09:00:00+08:00");
    assertEquals("historical", ProposalRelevance.assess(item, now));
    item.put("operation", "cancel");
    assertEquals("current", ProposalRelevance.assess(item, now));
  }

  @Test
  public void unrelatedContextCannotMakeHistoricalWorkCurrent() throws Exception {
    long now = java.time.Instant.parse("2026-11-01T01:00:00Z").toEpochMilli();
    JSONObject anchor = source().put("created_at", 1717570800000L);
    anchor.put("evidence", new JSONArray().put(source().put("created_at", now)
        .put("_supporting_evidence", false)));
    assertEquals("uncertain", ProposalRelevance.assess(proposal().put("source", anchor), now));
    anchor.getJSONArray("evidence").getJSONObject(0).put("_supporting_evidence", true);
    assertEquals("current", ProposalRelevance.assess(proposal().put("source", anchor), now));
    anchor.getJSONArray("evidence").getJSONObject(0).put("_context_only", true);
    assertEquals("uncertain", ProposalRelevance.assess(proposal().put("source", anchor), now));
  }

  @Test
  public void deduplicatesSameProposalAcrossJsonKeyOrder() throws Exception {
    JSONObject one = proposal(), two = new JSONObject();
    java.util.List<String> keys = new java.util.ArrayList<>();
    for (java.util.Iterator<String> iterator = one.keys(); iterator.hasNext(); )
      keys.add(iterator.next());
    java.util.Collections.reverse(keys);
    for (String key : keys) two.put(key, one.get(key));
    assertEquals(
        validate(one, new LinkedHashMap<>()).getJSONObject(0).getString("review_ref"),
        validate(two, new LinkedHashMap<>()).getJSONObject(0).getString("review_ref"));
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsFabricatedReferences() throws Exception {
    validate(proposal().put("source_refs", new JSONArray().put("invented")), new LinkedHashMap<>());
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsUnknownTarget() throws Exception {
    validate(
        proposal()
            .put("operation", "cancel")
            .put("target_id", "work:unknown")
            .put("expected_revision", 2),
        targets());
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsStaleTargetRevision() throws Exception {
    validate(
        proposal()
            .put("operation", "complete")
            .put("target_id", "work:1")
            .put("expected_revision", 1),
        targets());
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsCrossRoomChange() throws Exception {
    Map<String, JSONObject> targets = targets();
    targets.get("work:1").put("talker", "another-room");
    validate(
        proposal()
            .put("operation", "cancel")
            .put("target_id", "work:1")
            .put("expected_revision", 2),
        targets);
  }

  @Test
  public void retainsOtherPersonAttribution() throws Exception {
    assertEquals(
        "other",
        validate(
                proposal().put("kind", "memory").put("involvement", "other"), new LinkedHashMap<>())
            .getJSONObject(0)
            .getString("involvement"));
  }

  @Test
  public void acceptsEvidenceBackedUpdate() throws Exception {
    JSONObject item =
        validate(
                proposal()
                    .put("operation", "update")
                    .put("target_id", "work:1")
                    .put("expected_revision", 2)
                    .put("due_at", "2026-10-13T18:30:00+08:00"),
                targets())
            .getJSONObject(0);
    assertEquals("2026-10-13T10:30:00Z", item.getString("due_at"));
  }

  @Test(expected = IllegalArgumentException.class)
  public void creationCannotOverwrite() throws Exception {
    validate(proposal().put("target_id", "work:1"), targets());
  }

  @Test
  public void contextCanBeCitedWithoutAdvancingOldMessages() throws Exception {
    JSONObject old = source().put("msg_id", 2).put("_context_only", true).put("content", "原先约周五");
    JSONObject current = source().put("content", "改成下周一");
    AnalysisPayload payload =
        AnalysisPayload.prepare(
            new JSONArray().put(old).put(current),
            new JSONArray(),
            new JSONArray(),
            "Asia/Shanghai");
    assertEquals(1, payload.references().length());
    assertEquals(MemoryStore.reference(current), payload.references().getString(0));
    assertTrue(payload.input.getJSONArray("records").getJSONObject(0).getBoolean("context_only"));
    JSONObject item =
        proposal()
            .put("kind", "memory")
            .put("details", "原先约周五，后来明确改到下周一，尚未完成。")
            .put(
                "source_refs",
                new JSONArray()
                    .put(MemoryStore.reference(old))
                    .put(MemoryStore.reference(current)));
    JSONObject result =
        payload.validate(new JSONArray().put(item)).getJSONArray("items").getJSONObject(0);
    assertEquals(
        item.getString("details"),
        result.getJSONObject("source").getString("_information_summary"));
    assertEquals(2, result.getJSONObject("source").getJSONArray("evidence").length());
  }

  @Test(expected = IllegalArgumentException.class)
  public void contextAloneCannotCreateAnotherSuggestion() throws Exception {
    JSONObject old = source().put("_context_only", true);
    Map<String, JSONObject> evidence = new LinkedHashMap<>();
    evidence.put(MemoryStore.reference(old), old);
    AnalysisContract.validate(
        new JSONArray().put(proposal()), evidence, new LinkedHashMap<>(), "Asia/Shanghai");
  }

  @Test(expected = IllegalArgumentException.class)
  public void creationCannotCombineUnrelatedConversations() throws Exception {
    JSONObject other = source().put("msg_id", 2).put("talker", "unrelated");
    Map<String, JSONObject> evidence = allowed();
    evidence.put(MemoryStore.reference(other), other);
    JSONObject item =
        proposal()
            .put(
                "source_refs",
                new JSONArray()
                    .put(MemoryStore.reference(source()))
                    .put(MemoryStore.reference(other)));
    AnalysisContract.validate(
        new JSONArray().put(item), evidence, new LinkedHashMap<>(), "Asia/Shanghai");
  }

  @Test
  public void historicalBatchDoesNotSeeFutureKnownWork() throws Exception {
    JSONObject known =
        targets()
            .get("work:1")
            .put("title", "未来事项")
            .put("state", "open")
            .put("involvement", "self");
    known.put("source_json", source().put("created_at", 1791799200001L).toString());
    AnalysisPayload future =
        AnalysisPayload.prepare(
            new JSONArray().put(source()),
            new JSONArray().put(known),
            new JSONArray(),
            "Asia/Shanghai");
    assertEquals(0, future.input.getJSONArray("known_items").length());
    known.put("source_json", source().put("created_at", 1791799199000L).toString());
    assertEquals(
        1,
        AnalysisPayload.prepare(
                new JSONArray().put(source()),
                new JSONArray().put(known),
                new JSONArray(),
                "Asia/Shanghai")
            .input
            .getJSONArray("known_items")
            .length());
  }
}
