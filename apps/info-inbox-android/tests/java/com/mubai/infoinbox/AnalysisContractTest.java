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
}
