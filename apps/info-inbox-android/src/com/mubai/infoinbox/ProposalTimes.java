package com.mubai.infoinbox;

import org.json.JSONObject;

/** Show the extracted absolute date before confirmation; preserve ambiguous source wording. */
public final class ProposalTimes {
  private ProposalTimes() {}

  public static String describe(JSONObject item) {
    StringBuilder result = new StringBuilder();
    String zone = item.optString("timezone", "Asia/Shanghai");
    for (String key : new String[] {"due_at", "starts_at", "ends_at"}) {
      if (!item.isNull(key) && !item.optString(key).isEmpty()) {
        Long value = TimeRules.parseIso(item.optString(key));
        if (result.length() > 0) result.append('\n');
        result
            .append(key.equals("due_at") ? "截止：" : key.equals("starts_at") ? "开始：" : "结束：")
            .append(TimeRules.display(value, zone));
      }
    }
    String wording = item.optString("due_text", "");
    if (!wording.isEmpty() && !wording.equals("null")) {
      if (result.length() > 0) result.append('\n');
      result.append(result.length() == 0 ? "时间待确认：" : "时间原文：").append(wording);
    }
    return result.toString();
  }
}
