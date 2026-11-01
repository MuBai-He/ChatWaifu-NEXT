package com.mubai.infoinbox;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.ZoneId;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** Provider-neutral proposals. Model output never executes an operation. */
public final class AnalysisContract {
  private AnalysisContract() {}

  public static String hash(String value) throws Exception {
    StringBuilder out = new StringBuilder();
    for (byte b :
        MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))) {
      out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
    }
    return out.toString();
  }

  private static String string(JSONObject item, String key, int limit) throws Exception {
    Object value = item.get(key);
    if (!(value instanceof String) || ((String) value).length() > limit) {
      throw new IllegalArgumentException("模型字段格式无效：" + key);
    }
    return ((String) value).trim();
  }

  public static JSONArray validate(
      JSONArray items,
      Map<String, JSONObject> allowed,
      Map<String, JSONObject> targets,
      String timezone)
      throws Exception {
    ZoneId.of(timezone);
    if (items.length() > 12) throw new IllegalArgumentException("模型返回事项数量超出限制");
    JSONArray result = new JSONArray();
    for (int i = 0; i < items.length(); i++) {
      JSONObject input = items.getJSONObject(i);
      String title = string(input, "title", 200);
      String kind = string(input, "kind", 30);
      String importance = string(input, "importance", 10);
      String involvement = string(input, "involvement", 10);
      String reason = string(input, "reason", 1200);
      String operation = input.optString("operation", "create");
      if (title.isEmpty()
          || !kind.matches("todo|event|memory|decision|reference")
          || !importance.matches("high|medium|low")
          || !involvement.matches("self|other|unclear")
          || !operation.matches("create|update|complete|cancel")) {
        throw new IllegalArgumentException("模型结果不符合事项约定");
      }
      JSONArray refs = input.getJSONArray("source_refs");
      if (refs.length() < 1 || refs.length() > 10) throw new IllegalArgumentException("模型结果缺少有效出处");
      JSONArray evidence = new JSONArray();
      Set<String> seen = new HashSet<>();
      for (int j = 0; j < refs.length(); j++) {
        String ref = refs.getString(j);
        JSONObject source = allowed.get(ref);
        if (source == null) throw new IllegalArgumentException("模型引用了输入之外的消息");
        if (seen.add(ref)) evidence.put(new JSONObject(source.toString()));
      }
      JSONObject anchor = null;
      String account = evidence.getJSONObject(0).getString("account_id");
      String talker = evidence.getJSONObject(0).getString("talker");
      for (int j = 0; j < evidence.length(); j++) {
        JSONObject part = evidence.getJSONObject(j);
        if (!account.equals(part.getString("account_id"))
            || !talker.equals(part.getString("talker")))
          throw new IllegalArgumentException("一条建议不能混合不同会话的上下文");
        if (!part.optBoolean("_context_only") && anchor == null) anchor = part;
      }
      if (anchor == null) throw new IllegalArgumentException("建议必须引用本轮的新消息");
      java.util.List<JSONObject> window = new java.util.ArrayList<>();
      for (JSONObject part : allowed.values())
        if (account.equals(part.optString("account_id")) && talker.equals(part.optString("talker")))
          window.add(
              new JSONObject(part.toString())
                  .put("_supporting_evidence", seen.contains(MemoryStore.reference(part))));
      window.sort(
          (a, b) -> {
            int time = Long.compare(a.optLong("created_at"), b.optLong("created_at"));
            return time != 0 ? time : Long.compare(a.optLong("msg_id"), b.optLong("msg_id"));
          });
      evidence = new JSONArray();
      JSONArray contextRefs = new JSONArray();
      for (JSONObject part : window) {
        evidence.put(part);
        contextRefs.put(MemoryStore.reference(part));
      }
      JSONObject primary = new JSONObject(anchor.toString());
      String targetId = input.optString("target_id", "");
      int expectedRevision = 0;
      if (!operation.equals("create")) {
        JSONObject target = targets.get(targetId);
        if (target == null || !kind.matches("todo|event")) {
          throw new IllegalArgumentException("模型引用了未知事项");
        }
        Object revision = input.get("expected_revision");
        if (!(revision instanceof Number)
            || ((Number) revision).doubleValue() != target.getInt("revision")) {
          throw new IllegalArgumentException("模型事项版本无效");
        }
        expectedRevision = target.getInt("revision");
        for (int j = 0; j < evidence.length(); j++) {
          JSONObject source = evidence.getJSONObject(j);
          if (!source.getString("account_id").equals(target.getString("account_id"))
              || !source.getString("talker").equals(target.getString("talker"))) {
            throw new IllegalArgumentException("变更建议来自其他账号或会话");
          }
        }
        if (!kind.equals(target.getString("kind"))) throw new IllegalArgumentException("事项类型不一致");
      } else if (!targetId.isEmpty()) {
        throw new IllegalArgumentException("新建建议不能覆盖已有事项");
      }
      Long due = nullableTime(input, "due_at");
      Long start = nullableTime(input, "starts_at");
      Long end = nullableTime(input, "ends_at");
      TimeRules.validateRange(start, end);
      String state = input.optString("state", "open");
      if (!state.matches("open|waiting|blocked")) throw new IllegalArgumentException("事项状态无效");
      if (!operation.equals("create")
          && !operation.equals("update")
          && (due != null || start != null || end != null)) {
        throw new IllegalArgumentException("完成或取消不应修改日期");
      }
      JSONObject item =
          new JSONObject()
              .put("schema_version", "1.1")
              .put("title", title)
              .put("kind", kind)
              .put("importance", importance)
              .put("reason", reason)
              .put("involvement", involvement)
              .put("operation", operation)
              .put("target_id", targetId)
              .put("expected_revision", expectedRevision)
              .put("state", state)
              .put("timezone", timezone)
              .put("source_refs", refs)
              .put("context_refs", contextRefs)
              .put("due_at", due == null ? JSONObject.NULL : TimeRules.iso(due))
              .put("starts_at", start == null ? JSONObject.NULL : TimeRules.iso(start))
              .put("ends_at", end == null ? JSONObject.NULL : TimeRules.iso(end))
              .put(
                  "due_text",
                  input.isNull("due_text") ? JSONObject.NULL : input.optString("due_text", ""));
      item.put("location", input.has("location") ? string(input, "location", 200) : "");
      item.put("details", input.has("details") ? string(input, "details", 1200) : "");
      for (String key : new String[] {"due_date", "start_date", "end_date"})
        if (!input.isNull(key) && !input.optString(key).isEmpty())
          item.put(key, java.time.LocalDate.parse(input.getString(key)).toString());
      if (!item.optString("details").isEmpty() && !kind.matches("todo|event"))
        primary.put("_information_summary", item.getString("details"));
      String id = "ai:" + hash(item.toString());
      primary.remove("_source_ref");
      primary
          .put("_memory_ref", id)
          .put("evidence", evidence)
          .put("_information_involvement", involvement)
          .put("_information_kind", kind);
      item.put("source", primary).put("review_ref", id);
      // Relevance is derived from evidence, never trusted from model output, and
      // excluded from proposal identity so the same result cannot duplicate daily.
      ProposalRelevance.annotate(item, System.currentTimeMillis());
      result.put(item);
    }
    return result;
  }

  private static Long nullableTime(JSONObject input, String key) throws Exception {
    if (!input.has(key) || input.isNull(key)) return null;
    return TimeRules.parseIso(string(input, key, 50));
  }
}
