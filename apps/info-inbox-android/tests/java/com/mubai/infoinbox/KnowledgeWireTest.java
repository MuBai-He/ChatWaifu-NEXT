package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class KnowledgeWireTest {
  private JSONObject source(String text) throws Exception {
    return new JSONObject()
        .put("account_id", "synthetic-owner")
        .put("msg_id", 1)
        .put("talker", "synthetic@chatroom")
        .put("content", text)
        .put("created_at", 1791800000000L)
        .put("is_send", 0)
        .put("conversation_title", "合成测试群")
        .put("sender_name", "小林");
  }

  @Test
  public void identitySurvivesRevisionAndNicknameChanges() throws Exception {
    JSONObject first = KnowledgeWire.sourceRecords(source("person_a:\n周四开会")).getJSONObject(0);
    JSONObject second =
        KnowledgeWire.sourceRecords(source("person_a:\n改为周五").put("sender_name", "林同学"))
            .getJSONObject(0);
    assertEquals(first.getString("record_id"), second.getString("record_id"));
    assertEquals(first.getString("sender_id"), second.getString("sender_id"));
    assertNotEquals(KnowledgeWire.fingerprint(first), KnowledgeWire.fingerprint(second));
    JSONObject other = KnowledgeWire.sourceRecords(source("person_b:\n周四开会")).getJSONObject(0);
    assertNotEquals(first.getString("sender_id"), other.getString("sender_id"));
  }

  @Test
  public void longUnicodeMessageIsCompleteAndStable() throws Exception {
    StringBuilder text = new StringBuilder();
    for (int i = 0; i < 60001; i++) text.append("😀");
    JSONArray records = KnowledgeWire.sourceRecords(source("person_a:\n" + text));
    StringBuilder restored = new StringBuilder();
    for (int i = 0; i < records.length(); i++) {
      String part = records.getJSONObject(i).getString("text");
      assertTrue(part.length() <= 60000);
      assertFalse(Character.isHighSurrogate(part.charAt(part.length() - 1)));
      restored.append(part);
    }
    assertEquals(text.toString(), restored.toString());
  }

  @Test
  public void canonicalFingerprintIgnoresObjectKeyOrderAndServerMetadata() throws Exception {
    JSONObject first =
        new JSONObject().put("a", 1).put("nested", new JSONObject().put("b", "资料").put("a", true));
    JSONObject second =
        new JSONObject()
            .put("nested", new JSONObject().put("a", true).put("b", "资料"))
            .put("a", 1)
            .put("revision", 9)
            .put("updated_at", 12)
            .put("deleted", false);
    assertEquals(KnowledgeWire.fingerprint(first), KnowledgeWire.fingerprint(second));
  }
}
