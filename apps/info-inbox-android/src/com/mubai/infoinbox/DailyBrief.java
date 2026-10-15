package com.mubai.infoinbox;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import org.json.JSONArray;
import org.json.JSONObject;

/** Source-backed overview; absence of a completion record is never proof of failure. */
public final class DailyBrief {
  private DailyBrief() {}

  public static JSONObject build(JSONArray items, String timezone, long now) throws Exception {
    ZoneId zone = ZoneId.of(timezone);
    LocalDate today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate();
    long start = today.atStartOfDay(zone).toInstant().toEpochMilli();
    long end = today.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli();
    JSONArray todayItems = new JSONArray(),
        overdue = new JSONArray(),
        waiting = new JSONArray(),
        undated = new JSONArray();
    for (int i = 0; i < items.length(); i++) {
      JSONObject item = items.getJSONObject(i);
      String state = item.getString("state");
      if (state.matches("completed|cancelled")) continue;
      Long at =
          item.getString("kind").equals("event")
              ? OrganizerStore.nullable(item, "starts_at")
              : OrganizerStore.nullable(item, "due_at");
      if (state.matches("waiting|blocked")) waiting.put(item);
      if (at == null) undated.put(item);
      else if (at >= start && at < end) todayItems.put(item);
      else if (at < start) overdue.put(item);
    }
    return new JSONObject()
        .put("today", todayItems)
        .put("past_due", overdue)
        .put("waiting", waiting)
        .put("undated", undated);
  }
}
