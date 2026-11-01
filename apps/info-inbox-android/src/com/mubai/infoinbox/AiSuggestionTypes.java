package com.mubai.infoinbox;

import org.json.JSONArray;
import org.json.JSONObject;

/** View categories preserve the proposal's original kind and confirmation contract. */
public final class AiSuggestionTypes {
  public static final String[] SECTIONS = {"all", "memory", "reference", "work", "history"};

  private AiSuggestionTypes() {}

  public static String sectionLabel(String section) {
    return section.equals("memory")
        ? "记忆"
        : section.equals("reference") ? "资料" : section.equals("work") ? "事项"
        : section.equals("history") ? "历史" : "当前";
  }

  private static String section(JSONObject item) {
    String kind = item.optString("kind");
    if (ProposalRelevance.needsHistoricalReview(item)) return "history";
    if (kind.matches("todo|event")) return "work";
    if (kind.equals("reference")) return "reference";
    if (kind.matches("memory|fact|preference|decision")) return "memory";
    return "other";
  }

  public static String label(JSONObject item) {
    if (ProposalRelevance.needsHistoricalReview(item))
      return "历史核对 · " + (item.optString("kind").equals("event") ? "日程" : "待办");
    switch (item.optString("kind")) {
      case "todo":
        return "事项 · 待办";
      case "event":
        return "事项 · 日程";
      case "memory":
        return "记忆";
      case "fact":
        return "记忆 · 事实";
      case "preference":
        return "记忆 · 偏好";
      case "decision":
        return "记忆 · 决定";
      case "reference":
        return "资料";
      default:
        return "待分类";
    }
  }

  public static JSONArray filter(JSONArray items, String section) throws Exception {
    java.util.List<JSONObject> result = new java.util.ArrayList<>();
    for (int i = 0; i < items.length(); i++) {
      JSONObject item = items.getJSONObject(i);
      if ((section.equals("all") && !section(item).equals("history"))
          || section(item).equals(section)) result.add(item);
    }
    if (section.equals("history"))
      result.sort((a, b) -> Long.compare(sourceTime(b), sourceTime(a)));
    return new JSONArray(result);
  }

  private static long sourceTime(JSONObject item) {
    JSONObject source = item.optJSONObject("source");
    return source == null ? 0 : source.optLong("created_at");
  }

  public static int color(JSONObject item) {
    if (section(item).equals("work")) return 0xFF23795D;
    if (section(item).equals("reference")) return 0xFF8A5F16;
    return 0xFF6152A5;
  }

  public static int background(JSONObject item) {
    if (section(item).equals("work")) return 0xFFE7F3ED;
    if (section(item).equals("reference")) return 0xFFFFF2D8;
    return 0xFFF0EDF8;
  }
}
