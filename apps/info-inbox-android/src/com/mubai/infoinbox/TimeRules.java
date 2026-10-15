package com.mubai.infoinbox;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.ResolverStyle;
import java.util.List;
import java.util.Locale;

/** Strict dates are anchored to an explicit zone, never the time of analysis. */
public final class TimeRules {
  private static final DateTimeFormatter LOCAL =
      DateTimeFormatter.ofPattern("uuuu-MM-dd HH:mm", Locale.ROOT)
          .withResolverStyle(ResolverStyle.STRICT);

  private TimeRules() {}

  public static Long parseIso(String value) {
    if (value == null || value.trim().isEmpty()) return null;
    long time = OffsetDateTime.parse(value).toInstant().toEpochMilli();
    int year = Instant.ofEpochMilli(time).atOffset(ZoneOffset.UTC).getYear();
    if (year < 2000 || year > 2100) throw new IllegalArgumentException("日期超出支持范围");
    return time;
  }

  public static Long parseLocal(String value, String timezone) {
    if (value == null || value.trim().isEmpty()) return null;
    LocalDateTime local = LocalDateTime.parse(value.trim(), LOCAL);
    List<ZoneOffset> offsets = ZoneId.of(timezone).getRules().getValidOffsets(local);
    if (offsets.size() != 1) throw new IllegalArgumentException("该本地时间不存在或有歧义，请选择其他时间");
    return parseIso(local.atOffset(offsets.get(0)).toString());
  }

  public static String display(Long time, String timezone) {
    return time == null ? "" : LOCAL.format(Instant.ofEpochMilli(time).atZone(ZoneId.of(timezone)));
  }

  public static String iso(Long time) {
    return time == null ? null : Instant.ofEpochMilli(time).toString();
  }

  public static void validateRange(Long start, Long end) {
    if (end != null && start == null) throw new IllegalArgumentException("请同时填写计划或日程的开始时间");
    if (start != null && end != null && (end <= start || end - start > 31L * 86400000L)) {
      throw new IllegalArgumentException("结束时间应晚于开始时间，且相隔不超过 31 天");
    }
  }
}
