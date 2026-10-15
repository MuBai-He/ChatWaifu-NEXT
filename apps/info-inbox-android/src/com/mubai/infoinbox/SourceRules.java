package com.mubai.infoinbox;

import java.util.regex.Pattern;

/** Local candidate labels only. These are not AI conclusions or completed memories. */
public final class SourceRules {
  private static final Pattern TASK =
      Pattern.compile("(记得|麻烦|需要你|请你|帮我|提交|确认一下|截止|到期|约定|会议|面试|验收|待办|提醒)");
  private static final Pattern FACT = Pattern.compile("(决定|确定了|定下来|以后|一直|习惯|喜欢|偏好|地址是|账号是|联系方式)");

  private SourceRules() {}

  public static String label(String content) {
    if (content == null) return "消息";
    if (TASK.matcher(content).find()) return "待办线索";
    if (FACT.matcher(content).find()) return "记忆线索";
    return "消息";
  }

  public static String shellQuote(String value) {
    if (value.indexOf('\0') >= 0)
      throw new IllegalArgumentException("NUL is not valid in a shell argument");
    return "'" + value.replace("'", "'\\''") + "'";
  }

  public static String sqlQuote(String value) {
    if (value.indexOf('\0') >= 0)
      throw new IllegalArgumentException("NUL is not valid in a SQL literal");
    return "'" + value.replace("'", "''") + "'";
  }
}
