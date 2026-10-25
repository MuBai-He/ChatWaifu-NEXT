package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.junit.Test;

public class TimeRulesTest {
  @Test
  public void explicitZoneRoundTrips() {
    Long value = TimeRules.parseLocal("2026-10-13 18:30", "Asia/Shanghai");
    assertEquals("2026-10-13T10:30:00Z", TimeRules.iso(value));
    assertEquals("2026-10-13 18:30", TimeRules.display(value, "Asia/Shanghai"));
  }

  @Test
  public void missingDateStaysUnknown() {
    assertNull(TimeRules.parseLocal("", "Asia/Shanghai"));
    assertNull(TimeRules.parseIso(null));
  }

  @Test(expected = RuntimeException.class)
  public void rejectsInvalidCalendarDate() {
    TimeRules.parseLocal("2026-02-30 18:30", "Asia/Shanghai");
  }

  @Test(expected = RuntimeException.class)
  public void requiresIsoOffset() {
    TimeRules.parseIso("2026-10-13T18:30:00");
  }

  @Test(expected = RuntimeException.class)
  public void rejectsDstGap() {
    TimeRules.parseLocal("2026-03-08 02:30", "America/New_York");
  }

  @Test(expected = RuntimeException.class)
  public void rejectsDstOverlap() {
    TimeRules.parseLocal("2026-11-01 01:30", "America/New_York");
  }

  @Test(expected = RuntimeException.class)
  public void rejectsReversedEvent() {
    TimeRules.validateRange(100L, 99L);
  }

  @Test(expected = RuntimeException.class)
  public void rejectsUnboundedEvent() {
    TimeRules.validateRange(100L, 100L + 32L * 86400000L);
  }

  @Test(expected = IllegalArgumentException.class)
  public void refusesEndWithoutStart() {
    TimeRules.validateRange(null, 1792000000000L);
  }
}
