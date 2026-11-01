package com.mubai.infoinbox;

import java.util.LinkedHashMap;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

/** The same bounded evidence and local validation apply to Runtime and standalone modes. */
public final class AnalysisPayload {
  public final JSONObject input;
  public final Map<String, JSONObject> allowed = new LinkedHashMap<>(),
      targets = new LinkedHashMap<>();
  public final String timezone;
  private final JSONArray backgroundSources = new JSONArray();

  private AnalysisPayload(String timezone) {
    this.timezone = timezone;
    input = new JSONObject();
  }

  public static AnalysisPayload prepare(
      JSONArray sources, JSONArray knownItems, JSONArray prior, String timezone) throws Exception {
    AnalysisPayload data = new AnalysisPayload(timezone);
    JSONArray records = new JSONArray();
    int chars = 0;
    for (int i = 0; i < sources.length() && i < 30; i++) {
      JSONObject source = sources.getJSONObject(i);
      String ref = MemoryStore.reference(source), text = MessagePresentation.body(source);
      if (text.length() > 3000) throw new IllegalArgumentException("长消息需先分段，未推进处理状态");
      if (chars + text.length() > 16000 && records.length() > 0) break;
      chars += text.length();
      data.allowed.put(ref, source);
      records.put(
          new JSONObject()
              .put("ref", ref)
              .put("room_ref", room(source))
              .put("conversation", source.optString("conversation_title"))
              .put("sent_by_owner", source.optInt("is_send") == 1)
              .put("sender_name", speakerName(source))
              .put("sender_ref", speakerRef(source))
              .put("context_only", source.optBoolean("_context_only"))
              .put("message_time_ms", source.optLong("created_at"))
              .put("text", text)
              .put("segment", source.optInt("segment", 1))
              .put("text_truncated", false)
              .put("previous_segment_context", source.optString("_segment_prefix", "")));
    }
    long freshTime = Long.MAX_VALUE;
    for (JSONObject source : data.allowed.values())
      if (!source.optBoolean("_context_only"))
        freshTime = Math.min(freshTime, source.optLong("created_at"));
    JSONArray known = new JSONArray();
    for (int i = 0; i < knownItems.length() && known.length() < 20; i++) {
      JSONObject item = knownItems.getJSONObject(i);
      boolean same = false;
      for (JSONObject source : data.allowed.values()) same |= room(source).equals(room(item));
      if (!same) continue;
      if (item.has("source_json")) {
        JSONObject provenance = new JSONObject(item.getString("source_json"));
        long time = provenance.optLong("created_at");
        JSONArray evidence = provenance.optJSONArray("evidence");
        if (evidence != null)
          for (int j = 0; j < evidence.length(); j++)
            time = Math.max(time, evidence.getJSONObject(j).optLong("created_at"));
        if (time > freshTime) continue;
      }
      data.targets.put(item.getString("id"), item);
      known.put(
          new JSONObject()
              .put("id", item.getString("id"))
              .put("revision", item.getInt("revision"))
              .put("kind", item.getString("kind"))
              .put("title", item.getString("title"))
              .put("details", item.optString("details"))
              .put("state", item.getString("state"))
              .put("room_ref", room(item))
              .put("involvement", item.getString("involvement"))
              .put("due_at", TimeRules.iso(OrganizerStore.nullable(item, "due_at")))
              .put("starts_at", TimeRules.iso(OrganizerStore.nullable(item, "starts_at")))
              .put("ends_at", TimeRules.iso(OrganizerStore.nullable(item, "ends_at"))));
    }
    JSONArray context = new JSONArray(), knowledge = new JSONArray();
    int budget = 0, knowledgeBudget = 0;
    for (int i = 0; i < prior.length(); i++) {
      JSONObject source = prior.getJSONObject(i);
      boolean same = false;
      for (JSONObject allowed : data.allowed.values()) same |= room(source).equals(room(allowed));
      if (!same) continue;
      if (source.optString("_context_type").equals("knowledge")) {
        String summary = source.getString("summary");
        if (knowledge.length() < 10 && knowledgeBudget + summary.length() <= 6000) {
          knowledge.put(
              new JSONObject()
                  .put("id", source.getString("id"))
                  .put("room_ref", room(source))
                  .put("kind", source.getString("kind"))
                  .put("summary", summary)
                  .put("state", source.getString("state"))
                  .put("source_time_ms", source.getLong("source_time_ms")));
          JSONArray evidence =
              OrganizerStore.contextEvidence(source.getJSONObject("_context_source"));
          for (int j = 0; j < evidence.length(); j++)
            data.backgroundSources.put(evidence.getJSONObject(j));
          knowledgeBudget += summary.length();
        }
        continue;
      }
      if (context.length() >= 8) continue;
      String text = source.optString("content", "");
      if (text.length() > 1000) text = text.substring(0, 1000);
      if (budget + text.length() > 6000) break;
      budget += text.length();
      context.put(
          new JSONObject()
              .put("room_ref", room(source))
              .put("conversation", source.optString("conversation_title"))
              .put("sent_by_owner", source.optInt("is_send") == 1)
              .put("message_time_ms", source.optLong("created_at"))
              .put("text", text));
    }
    data.input
        .put("timezone", timezone)
        .put("schema_version", "1.2")
        .put(
            "context_incomplete",
            sources.length() > records.length() || hasIncompleteContext(sources))
        .put("records", records)
        .put("known_items", known)
        .put("prior_context", context);
    if (knowledge.length() > 0) data.input.put("prior_knowledge", knowledge);
    return data;
  }

  private static String speakerName(JSONObject source) {
    String name = MessagePresentation.senderName(source);
    if (!name.isEmpty()) return name;
    return source.optString("talker").endsWith("@chatroom")
        ? "发言者未识别"
        : MessagePresentation.conversationTitle(source);
  }

  private static String speakerRef(JSONObject source) throws Exception {
    String member = MessagePresentation.senderKey(source);
    if (source.optInt("is_send") != 1
        && source.optString("talker").endsWith("@chatroom")
        && member.isEmpty()) return "";
    return AnalysisContract.hash(
        room(source)
            + "\0"
            + (source.optInt("is_send") == 1 ? "owner" : member.isEmpty() ? "peer" : member));
  }

  private static String room(JSONObject source) throws Exception {
    return AnalysisContract.hash(
        source.getString("account_id") + "\0" + source.getString("talker"));
  }

  public JSONArray references() {
    JSONArray refs = new JSONArray();
    for (String ref : allowed.keySet())
      if (!allowed.get(ref).optBoolean("_context_only")) refs.put(ref);
    return refs;
  }

  private static boolean hasIncompleteContext(JSONArray sources) throws Exception {
    for (int i = 0; i < sources.length(); i++)
      if (sources.getJSONObject(i).optBoolean("_context_incomplete")) return true;
    return false;
  }

  public JSONObject validate(JSONArray items) throws Exception {
    for (int i = 0; i < items.length(); i++)
      if (items.getJSONObject(i).optString("details").trim().isEmpty())
        throw new IllegalArgumentException("建议缺少完整结论，未推进处理进度");
    JSONArray validated = AnalysisContract.validate(items, allowed, targets, timezone);
    for (int i = 0; i < validated.length(); i++) {
      JSONObject source = validated.getJSONObject(i).getJSONObject("source");
      source.put("_analysis_context_version", "1.2");
      JSONArray evidence = source.getJSONArray("evidence");
      java.util.Set<String> ids = new java.util.HashSet<>();
      for (int j = 0; j < evidence.length(); j++)
        ids.add(OrganizerStore.contextKey(evidence.getJSONObject(j)));
      for (int j = 0; j < backgroundSources.length(); j++) {
        JSONObject part = backgroundSources.getJSONObject(j);
        if (ids.add(OrganizerStore.contextKey(part))) evidence.put(new JSONObject(part.toString()));
      }
      if (evidence.length() > 30) throw new IllegalStateException("历史依据超过预算，未推进处理进度");
      if (input.has("prior_knowledge"))
        source.put(
            "_prior_knowledge", new JSONArray(input.getJSONArray("prior_knowledge").toString()));
    }
    return new JSONObject()
        .put("schema_version", "1.2")
        .put("items", validated)
        .put("processed_refs", references());
  }
}
