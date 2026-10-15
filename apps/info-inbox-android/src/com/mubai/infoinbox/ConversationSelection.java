package com.mubai.infoinbox;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** Transient selection is scoped to the visible page, keyed by account and conversation. */
public final class ConversationSelection {
  private final Map<String, JSONObject> visible = new LinkedHashMap<>();
  private final Set<String> selected = new LinkedHashSet<>();

  public static String key(JSONObject room) {
    if (!(room.opt("account_id") instanceof String) || !(room.opt("talker") instanceof String))
      throw new IllegalArgumentException("会话身份格式无效");
    String account = room.optString("account_id"), talker = room.optString("talker");
    if (account.isEmpty() || talker.isEmpty()) throw new IllegalArgumentException("会话身份不完整");
    return account.length() + ":" + account + talker;
  }

  public void replaceVisible(JSONArray rooms) throws Exception {
    if (rooms.length() > 100) throw new IllegalArgumentException("每页最多选择 100 个会话");
    Map<String, JSONObject> next = new LinkedHashMap<>();
    for (int i = 0; i < rooms.length(); i++) {
      JSONObject room = rooms.getJSONObject(i);
      next.put(key(room), new JSONObject(room.toString()));
    }
    visible.clear();
    visible.putAll(next);
    selected.clear();
  }

  public void select(JSONObject room, boolean value) {
    String key = key(room);
    if (!visible.containsKey(key)) throw new IllegalArgumentException("会话不在当前页，请重新选择");
    if (value) selected.add(key);
    else selected.remove(key);
  }

  public boolean selected(JSONObject room) {
    return selected.contains(key(room));
  }

  public int count() {
    return selected.size();
  }

  public int visibleCount() {
    return visible.size();
  }

  public void all(boolean value) {
    selected.clear();
    if (value) selected.addAll(visible.keySet());
  }

  public JSONArray snapshot() throws Exception {
    JSONArray result = new JSONArray();
    for (String key : selected) result.put(new JSONObject(visible.get(key).toString()));
    return result;
  }
}
