package com.mubai.infoinbox;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** Selection retains only unchanged proposal snapshots on the current page. */
public final class AiReviewSelection {
  public static final int PAGE_SIZE = 100;
  private final Map<String, JSONObject> visible = new LinkedHashMap<>();
  private final Set<String> selected = new LinkedHashSet<>();

  public static String key(JSONObject item) {
    if (!(item.opt("review_ref") instanceof String))
      throw new IllegalArgumentException("建议身份不完整，请刷新列表");
    String ref = item.optString("review_ref");
    if (ref.isEmpty() || ref.length() > 200) throw new IllegalArgumentException("建议身份不完整，请刷新列表");
    return ref;
  }

  public void replaceVisible(JSONArray items) throws Exception {
    if (items.length() > PAGE_SIZE) throw new IllegalArgumentException("每页最多选择 100 条建议");
    Map<String, JSONObject> next = new LinkedHashMap<>();
    for (int i = 0; i < items.length(); i++) {
      JSONObject item = items.getJSONObject(i);
      next.put(key(item), new JSONObject(item.toString()));
    }
    selected.removeIf(
        ref ->
            !next.containsKey(ref)
                || !next.get(ref).toString().equals(visible.get(ref).toString()));
    visible.clear();
    visible.putAll(next);
  }

  public void select(JSONObject item, boolean value) {
    String ref = key(item);
    if (!visible.containsKey(ref)) throw new IllegalArgumentException("建议不在当前页，请刷新列表");
    if (value) selected.add(ref);
    else selected.remove(ref);
  }

  public boolean selected(JSONObject item) {
    return selected.contains(key(item));
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
    for (String ref : selected) result.put(new JSONObject(visible.get(ref).toString()));
    return result;
  }
}
