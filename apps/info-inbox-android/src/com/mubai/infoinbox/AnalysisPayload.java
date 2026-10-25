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
      String ref = MemoryStore.reference(source), text = source.optString("content", "");
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
              .put("message_time_ms", source.optLong("created_at"))
              .put("text", text)
              .put("segment", source.optInt("segment", 1))
              .put("text_truncated", false)
              .put("previous_segment_context", source.optString("_segment_prefix", "")));
    }
    JSONArray known = new JSONArray();
    for (int i = 0; i < knownItems.length() && known.length() < 20; i++) {
      JSONObject item = knownItems.getJSONObject(i);
      boolean same = false;
      for (JSONObject source : data.allowed.values()) same |= room(source).equals(room(item));
      if (!same) continue;
      data.targets.put(item.getString("id"), item);
      known.put(
          new JSONObject()
              .put("id", item.getString("id"))
              .put("revision", item.getInt("revision"))
              .put("kind", item.getString("kind"))
              .put("title", item.getString("title"))
              .put("state", item.getString("state"))
              .put("room_ref", room(item))
              .put("involvement", item.getString("involvement"))
              .put("due_at", TimeRules.iso(OrganizerStore.nullable(item, "due_at")))
              .put("starts_at", TimeRules.iso(OrganizerStore.nullable(item, "starts_at")))
              .put("ends_at", TimeRules.iso(OrganizerStore.nullable(item, "ends_at"))));
    }
    JSONArray context = new JSONArray();
    int budget = 0;
    for (int i = 0; i < prior.length() && context.length() < 8; i++) {
      JSONObject source = prior.getJSONObject(i);
      boolean same = false;
      for (JSONObject allowed : data.allowed.values()) same |= room(source).equals(room(allowed));
      if (!same) continue;
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
        .put("records", records)
        .put("known_items", known)
        .put("prior_context", context);
    return data;
  }

  private static String room(JSONObject source) throws Exception {
    return AnalysisContract.hash(
        source.getString("account_id") + "\0" + source.getString("talker"));
  }

  public JSONArray references() {
    JSONArray refs = new JSONArray();
    for (String ref : allowed.keySet()) refs.put(ref);
    return refs;
  }

  public JSONObject validate(JSONArray items) throws Exception {
    return new JSONObject()
        .put("schema_version", "1.1")
        .put("items", AnalysisContract.validate(items, allowed, targets, timezone))
        .put("processed_refs", references());
  }
}
