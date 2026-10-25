package com.mubai.infoinbox;

import org.json.JSONObject;

/** Human-facing labels only. The original message and reference identity remain unchanged. */
public final class MessagePresentation {
  private MessagePresentation() {}

  public static String senderKey(JSONObject source) {
    if (source.optInt("is_send") == 1 || !source.optString("talker").endsWith("@chatroom"))
      return "";
    String content = source.optString("content", "");
    int delimiter = content.indexOf(":\n");
    if (delimiter < 1 || delimiter > 128) return "";
    String key = content.substring(0, delimiter);
    return key.matches("[A-Za-z0-9_@.\\-]+") ? key : "";
  }

  public static String body(JSONObject source) {
    String content = source.optString("content", "");
    String sender = senderKey(source);
    return sender.isEmpty() ? content : content.substring(sender.length() + 2);
  }

  private static boolean technical(String value, String id) {
    return value.isEmpty()
        || value.equals(id)
        || value.matches("wxid_[A-Za-z0-9_]+")
        || value.endsWith("@chatroom");
  }

  private static String suffix(String id) {
    try {
      return AnalysisContract.hash(id).substring(0, 4).toUpperCase(java.util.Locale.ROOT);
    } catch (Exception unavailable) {
      throw new IllegalStateException("Unable to create a local display label", unavailable);
    }
  }

  public static String senderName(JSONObject source) {
    if (source.optInt("is_send") == 1) return "我";
    String key = senderKey(source);
    if (key.isEmpty()) return "";
    String name = source.optString("sender_name", "").trim();
    return technical(name, key) ? "群成员 · " + suffix(key) : name;
  }

  public static String conversationTitle(JSONObject source) {
    String id = source.optString("talker", "");
    String name = source.optString("conversation_title", source.optString("title", "")).trim();
    if (!technical(name, id)) return name;
    if (id.equals("filehelper")) return "文件传输助手";
    if (id.equals("weixin")) return "微信团队";
    return (id.endsWith("@chatroom") ? "群聊（名称待获取）" : "联系人（昵称待获取）") + " · " + suffix(id);
  }
}
