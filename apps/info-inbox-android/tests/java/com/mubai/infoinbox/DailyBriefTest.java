package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class DailyBriefTest {
  private JSONObject item(String state, Long time) throws Exception {
    return new JSONObject()
        .put("kind", "todo")
        .put("state", state)
        .put("due_at", time == null ? JSONObject.NULL : time);
  }

  @Test
  public void usesChosenTimezoneAndExcludesCompleted() throws Exception {
    long now = TimeRules.parseIso("2026-10-12T01:00:00Z");
    JSONArray items =
        new JSONArray()
            .put(item("open", TimeRules.parseIso("2026-10-11T17:00:00Z")))
            .put(item("open", TimeRules.parseIso("2026-10-11T01:00:00Z")))
            .put(item("waiting", null))
            .put(item("completed", TimeRules.parseIso("2026-10-11T17:00:00Z")));
    JSONObject brief = DailyBrief.build(items, "Asia/Shanghai", now);
    assertEquals(1, brief.getJSONArray("today").length());
    assertEquals(1, brief.getJSONArray("past_due").length());
    assertEquals(1, brief.getJSONArray("waiting").length());
    assertEquals(1, brief.getJSONArray("undated").length());
  }
}
