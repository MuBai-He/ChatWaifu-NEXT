package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.json.JSONObject;
import org.junit.Test;

public class MessagePresentationTest {
  private JSONObject group(String content) throws Exception {
    return new JSONObject()
        .put("talker", "12345@chatroom")
        .put("is_send", 0)
        .put("content", content);
  }

  @Test
  public void groupPrefixIsSeparatedWithoutChangingEvidence() throws Exception {
    JSONObject source = group("wxid_example:\n明天下午开会").put("sender_name", "王老师");
    assertEquals("明天下午开会", MessagePresentation.body(source));
    assertEquals("王老师", MessagePresentation.senderName(source));
    assertEquals("wxid_example:\n明天下午开会", source.getString("content"));
  }

  @Test
  public void directMessageAndOutgoingTextRemainLiteral() throws Exception {
    JSONObject source = group("wxid_example:\n正文");
    source.put("talker", "friend");
    assertEquals(source.getString("content"), MessagePresentation.body(source));
    source.put("talker", "12345@chatroom").put("is_send", 1);
    assertEquals(source.getString("content"), MessagePresentation.body(source));
    assertEquals("我", MessagePresentation.senderName(source));
  }

  @Test
  public void onlyRecognizedGroupSenderDelimiterIsRemoved() throws Exception {
    JSONObject source = group("通知：\n正文提到 wxid_example");
    assertEquals(source.getString("content"), MessagePresentation.body(source));
    source.put("content", "名称 含空格:\n内容");
    assertEquals(source.getString("content"), MessagePresentation.body(source));
  }

  @Test
  public void unreadableNamesUseStableLabelsRatherThanInventedNicknames() throws Exception {
    JSONObject source = group("wxid_unknown:\n内容");
    String name = MessagePresentation.senderName(source);
    assertTrue(name.startsWith("群成员 · "));
    assertFalse(name.contains("wxid"));
    assertEquals(name, MessagePresentation.senderName(source));
  }

  @Test
  public void rawConversationIdentifiersAreNotListTitles() throws Exception {
    JSONObject source = new JSONObject().put("talker", "wxid_unknown").put("title", "wxid_unknown");
    assertTrue(MessagePresentation.conversationTitle(source).startsWith("联系人（昵称待获取）"));
    source.put("title", "Kat");
    assertEquals("Kat", MessagePresentation.conversationTitle(source));
    source.put("talker", "filehelper").put("title", "filehelper");
    assertEquals("文件传输助手", MessagePresentation.conversationTitle(source));
  }
}
