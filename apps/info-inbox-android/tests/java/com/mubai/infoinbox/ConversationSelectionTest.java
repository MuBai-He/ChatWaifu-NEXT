package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class ConversationSelectionTest {
  private JSONObject room(String account, String talker) throws Exception {
    return new JSONObject().put("account_id", account).put("talker", talker).put("title", "合成会话");
  }

  @Test
  public void selectionUsesIdentityAndAccountRatherThanNames() throws Exception {
    JSONObject a = room("a", "friend"), b = room("b", "friend");
    ConversationSelection s = new ConversationSelection();
    s.replaceVisible(new JSONArray().put(a).put(b));
    assertEquals(0, s.count());
    s.select(a, true);
    assertTrue(s.selected(new JSONObject(a.toString()).put("title", "新备注")));
    assertFalse(s.selected(b));
    s.select(a, true);
    assertEquals(1, s.count());
    s.select(a, false);
    assertEquals(0, s.count());
    assertNotEquals(
        ConversationSelection.key(room("ab", "c")), ConversationSelection.key(room("a", "bc")));
  }

  @Test
  public void allSelectsOnlyVisiblePageAndSearchClearsOldSelection() throws Exception {
    JSONObject a = room("a", "first"), b = room("a", "second");
    ConversationSelection s = new ConversationSelection();
    s.replaceVisible(new JSONArray().put(a).put(a).put(b));
    s.all(true);
    assertEquals(2, s.count());
    assertEquals(2, s.visibleCount());
    s.replaceVisible(new JSONArray().put(b));
    assertEquals(0, s.count());
    s.all(true);
    assertEquals("second", s.snapshot().getJSONObject(0).getString("talker"));
    s.all(false);
    assertEquals(0, s.count());
  }

  @Test
  public void invisibleRoomsCannotBeAccidentallyIncluded() throws Exception {
    ConversationSelection s = new ConversationSelection();
    s.replaceVisible(new JSONArray().put(room("a", "shown")));
    assertThrows(IllegalArgumentException.class, () -> s.select(room("a", "hidden"), true));
    assertEquals(0, s.count());
  }

  @Test
  public void confirmationSnapshotIsIndependentOfFurtherSelectionChanges() throws Exception {
    JSONObject room = room("a", "first");
    ConversationSelection s = new ConversationSelection();
    s.replaceVisible(new JSONArray().put(room));
    s.all(true);
    JSONArray snapshot = s.snapshot();
    room.put("talker", "changed");
    snapshot.getJSONObject(0).put("talker", "changed");
    assertEquals("first", s.snapshot().getJSONObject(0).getString("talker"));
    s.replaceVisible(new JSONArray());
    assertEquals(0, s.count());
    assertEquals(1, snapshot.length());
  }

  @Test
  public void invalidOrOversizedPagesLeaveExistingSelectionIntact() throws Exception {
    ConversationSelection s = new ConversationSelection();
    s.replaceVisible(new JSONArray().put(room("a", "first")));
    s.all(true);
    assertThrows(
        IllegalArgumentException.class,
        () -> s.replaceVisible(new JSONArray().put(new JSONObject())));
    JSONArray tooMany = new JSONArray();
    for (int i = 0; i < 101; i++) tooMany.put(room("a", "room" + i));
    assertThrows(IllegalArgumentException.class, () -> s.replaceVisible(tooMany));
    assertEquals(1, s.count());
    assertEquals(1, s.visibleCount());
  }

  @Test
  public void malformedIdentityIsRejectedBeforeSelection() throws Exception {
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ConversationSelection.key(
                new JSONObject().put("account_id", 42).put("talker", "friend")));
    assertThrows(
        IllegalArgumentException.class, () -> ConversationSelection.key(room("", "friend")));
  }
}
