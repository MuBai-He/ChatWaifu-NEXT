package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.json.JSONObject;
import org.junit.Test;

public class ProposalTimesTest {
  @Test
  public void showsDeadlineAndEventClockSeparatelyInSourceZone() throws Exception {
    JSONObject item =
        new JSONObject()
            .put("timezone", "Asia/Shanghai")
            .put("due_at", "2026-10-15T07:00:00Z")
            .put("starts_at", "2026-10-14T10:00:00Z")
            .put("ends_at", "2026-10-14T11:00:00Z");
    assertEquals(
        "截止：2026-10-15 15:00\n开始：2026-10-14 18:00\n结束：2026-10-14 19:00",
        ProposalTimes.describe(item));
  }

  @Test
  public void vagueTimeStaysWordingWithoutInventingClock() throws Exception {
    assertEquals("时间待确认：过几天", ProposalTimes.describe(new JSONObject().put("due_text", "过几天")));
    assertEquals("", ProposalTimes.describe(new JSONObject().put("due_text", JSONObject.NULL)));
  }
}
