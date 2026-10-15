package com.mubai.infoinbox;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** Stable, provider-independent sync values. Never accepts executable source commands. */
public final class KnowledgeWire {
  private KnowledgeWire() {}

  public static String room(JSONObject source) throws Exception {
    return AnalysisContract.hash(
        source.getString("account_id") + "\0" + source.getString("talker"));
  }

  public static String canonical(Object value) throws Exception {
    if (value instanceof JSONObject) {
      JSONObject object = (JSONObject) value;
      List<String> keys = new ArrayList<>();
      Iterator<String> names = object.keys();
      while (names.hasNext()) keys.add(names.next());
      Collections.sort(keys);
      StringBuilder out = new StringBuilder("{");
      for (String key : keys) {
        if (out.length() > 1) out.append(',');
        out.append(JSONObject.quote(key)).append(':').append(canonical(object.get(key)));
      }
      return out.append('}').toString();
    }
    if (value instanceof JSONArray) {
      JSONArray array = (JSONArray) value;
      StringBuilder out = new StringBuilder("[");
      for (int i = 0; i < array.length(); i++) {
        if (i > 0) out.append(',');
        out.append(canonical(array.get(i)));
      }
      return out.append(']').toString();
    }
    return value instanceof String ? JSONObject.quote((String) value) : String.valueOf(value);
  }

  public static String fingerprint(JSONObject draft) throws Exception {
    JSONObject fields = new JSONObject(draft.toString());
    fields.remove("revision");
    fields.remove("deleted");
    fields.remove("updated_at");
    return AnalysisContract.hash(canonical(fields));
  }

  public static JSONObject draft(String id, String kind, String room, long time) throws Exception {
    return new JSONObject()
        .put("record_id", id)
        .put("kind", kind)
        .put("room_id", room)
        .put("title", "")
        .put("text", "")
        .put("state", "active")
        .put("confirmed", false)
        .put("occurred_at", time)
        .put("due_at", JSONObject.NULL)
        .put("starts_at", JSONObject.NULL)
        .put("ends_at", JSONObject.NULL)
        .put("remind_at", JSONObject.NULL)
        .put("timezone", "Asia/Shanghai")
        .put("source_refs", new JSONArray())
        .put("sender_id", JSONObject.NULL)
        .put("sender_name", "")
        .put("sent_by_owner", false)
        .put("origin", new JSONObject().put("source_type", "wechat_root"));
  }

  public static JSONArray sourceRecords(JSONObject source) throws Exception {
    String base = MemoryStore.reference(source);
    String text = MessagePresentation.body(source);
    String sender = source.optInt("is_send") == 1 ? "owner" : MessagePresentation.senderKey(source);
    if (sender.isEmpty()) sender = source.getString("talker");
    sender = "contact:" + AnalysisContract.hash(source.getString("account_id") + "\0" + sender);
    JSONArray records = new JSONArray();
    int offset = 0, part = 0;
    do {
      int end = Math.min(text.length(), offset + 60000);
      if (end < text.length() && Character.isHighSurrogate(text.charAt(end - 1))) end--;
      records.put(
          draft(base + ":part:" + part, "source", room(source), source.getLong("created_at"))
              .put("title", MessagePresentation.conversationTitle(source))
              .put("text", text.substring(offset, end))
              .put("sender_id", sender)
              .put(
                  "sender_name",
                  source.optInt("is_send") == 1
                      ? "我"
                      : source.getString("talker").endsWith("@chatroom")
                          ? MessagePresentation.senderName(source)
                          : MessagePresentation.conversationTitle(source))
              .put("sent_by_owner", source.optInt("is_send") == 1)
              .put(
                  "origin",
                  new JSONObject()
                      .put("source_type", "wechat_root")
                      .put("message_ref", base)
                      .put("part", part)));
      offset = end;
      part++;
    } while (offset < text.length());
    return records;
  }
}
