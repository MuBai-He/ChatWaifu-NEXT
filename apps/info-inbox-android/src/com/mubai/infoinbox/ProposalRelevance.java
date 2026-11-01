package com.mubai.infoinbox;

import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import org.json.JSONArray;
import org.json.JSONObject;

/** Review placement only: age never completes, deletes or rewrites confirmed work. */
public final class ProposalRelevance {
  private static final long RECENT = 7L * 86400000L;

  private ProposalRelevance() {}

  public static String assess(JSONObject item, long now) {
    if (!item.optString("kind").matches("todo|event")) return "current";
    try {
      JSONObject source = item.optJSONObject("source");
      if (source == null) return "uncertain";
      long latest = source.optBoolean("_context_only") ? 0 : source.optLong("created_at");
      JSONArray evidence = source.optJSONArray("evidence");
      for (int i = 0; evidence != null && i < evidence.length(); i++) {
        JSONObject part = evidence.getJSONObject(i);
        if (!part.optBoolean("_context_only")
            && part.optBoolean("_supporting_evidence", true))
          latest = Math.max(latest, part.optLong("created_at"));
      }
      if (latest <= 0 || latest > now + 300000L) return "uncertain";
      boolean recent = latest >= now - RECENT;
      if (recent && !item.optString("operation", "create").equals("create")) return "current";
      ZoneId zone = ZoneId.of(item.optString("timezone", "Asia/Shanghai"));
      LocalDate today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate();
      Long due = time(item, "due_at"), start = time(item, "starts_at"), end = time(item, "ends_at");
      LocalDate dueDay = day(item, "due_date"), startDay = day(item, "start_date"),
          endDay = day(item, "end_date");
      if (item.optString("kind").equals("event")
          && ((endDay != null && !endDay.isAfter(today))
              || (end != null && end <= now)
              || (endDay == null && startDay != null && startDay.isBefore(today))
              || (end == null && start != null
                  && Instant.ofEpochMilli(start).atZone(zone).toLocalDate().isBefore(today))))
        return "historical";
      if (recent) return "current";
      if ((due != null && due >= now) || (start != null && start >= now) || (end != null && end >= now)
          || (dueDay != null && !dueDay.isBefore(today))
          || (startDay != null && !startDay.isBefore(today))
          || (endDay != null && endDay.isAfter(today))) return "current";
      if ((due != null && due < now) || (dueDay != null && dueDay.isBefore(today))) return "historical";
      return "uncertain";
    } catch (RuntimeException failure) {
      return "uncertain";
    } catch (org.json.JSONException failure) {
      return "uncertain";
    }
  }

  private static Long time(JSONObject item, String key) {
    if (item.isNull(key) || item.optString(key).isEmpty()) return null;
    Object raw = item.opt(key);
    return raw instanceof Number ? ((Number) raw).longValue() : TimeRules.parseIso((String) raw);
  }

  private static LocalDate day(JSONObject item, String key) {
    return item.isNull(key) || item.optString(key).isEmpty() ? null : LocalDate.parse(item.optString(key));
  }

  public static void annotate(JSONObject item, long now) throws Exception {
    String status = assess(item, now);
    item.put("temporal_status", status);
    item.put("temporal_reason", status.equals("current") ? "" : status.equals("historical")
        ? "原文中的事项时间已过去，是否仍需处理需要重新核对。"
        : "原文较早或时间不完整，尚不能确认这件事现在仍需处理。");
  }

  public static boolean needsHistoricalReview(JSONObject item) {
    return item.optString("kind").matches("todo|event")
        && !item.optString("temporal_status", "current").equals("current");
  }
}
