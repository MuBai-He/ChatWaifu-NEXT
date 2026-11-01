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
  private final Map<String, String> fingerprints = new LinkedHashMap<>();

  public interface Snapshot {
    JSONArray copy() throws Exception;
  }

  public static final class Page {
    private final Map<String, JSONObject> values = new LinkedHashMap<>();
    private final Map<String, String> hashes = new LinkedHashMap<>();

    private Page() {}
  }

  public static Page prepare(JSONArray items) throws Exception {
    if (items.length() > PAGE_SIZE) throw new IllegalArgumentException("每页最多选择 100 条建议");
    Page page = new Page();
    for (int i = 0; i < items.length(); i++) {
      JSONObject item = items.getJSONObject(i);
      String ref = key(item), json = item.toString();
      page.values.put(ref, new JSONObject(json));
      page.hashes.put(ref, AnalysisContract.hash(json));
    }
    return page;
  }

  public static String key(JSONObject item) {
    if (!(item.opt("review_ref") instanceof String))
      throw new IllegalArgumentException("建议身份不完整，请刷新列表");
    String ref = item.optString("review_ref");
    if (ref.isEmpty() || ref.length() > 200) throw new IllegalArgumentException("建议身份不完整，请刷新列表");
    return ref;
  }

  public void replaceVisible(JSONArray items) throws Exception {
    replaceVisible(prepare(items));
  }

  public void replaceVisible(Page page) {
    selected.removeIf(
        ref ->
            !page.values.containsKey(ref) || !page.hashes.get(ref).equals(fingerprints.get(ref)));
    visible.clear();
    visible.putAll(page.values);
    fingerprints.clear();
    fingerprints.putAll(page.hashes);
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

  public Snapshot capture() {
    java.util.List<JSONObject> rows = new java.util.ArrayList<>();
    for (String ref : selected) rows.add(visible.get(ref));
    return () -> {
      JSONArray result = new JSONArray();
      for (JSONObject row : rows) result.put(new JSONObject(row.toString()));
      return result;
    };
  }

  public JSONArray snapshot() throws Exception {
    return capture().copy();
  }
}
