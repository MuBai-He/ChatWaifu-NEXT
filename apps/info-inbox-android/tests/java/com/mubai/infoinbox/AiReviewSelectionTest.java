package com.mubai.infoinbox;

import static org.junit.Assert.*;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class AiReviewSelectionTest {
  private JSONObject item(String id) throws Exception {
    return new JSONObject().put("review_ref", id).put("title", "同名建议");
  }

  @Test
  public void refreshKeepsOnlyUnchangedSelectionAndNeverSelectsNewArrivals() throws Exception {
    AiReviewSelection selection = new AiReviewSelection();
    JSONObject first = item("first"), second = item("second"), added = item("new");
    selection.replaceVisible(new JSONArray().put(first).put(second));
    selection.all(true);
    selection.replaceVisible(new JSONArray().put(added).put(second).put(first));
    assertEquals(2, selection.count());
    assertFalse(selection.selected(added));
    selection.replaceVisible(
        new JSONArray().put(new JSONObject(first.toString()).put("title", "修订")));
    assertEquals(0, selection.count());
  }

  @Test
  public void snapshotAndPageScopeCannotBeChangedByCaller() throws Exception {
    AiReviewSelection selection = new AiReviewSelection();
    JSONObject first = item("first");
    selection.replaceVisible(new JSONArray().put(first));
    selection.all(true);
    JSONArray snapshot = selection.snapshot();
    snapshot.getJSONObject(0).put("title", "改名");
    first.put("title", "改名");
    assertEquals("同名建议", selection.snapshot().getJSONObject(0).getString("title"));
    assertThrows(IllegalArgumentException.class, () -> selection.select(item("invisible"), true));
    selection.replaceVisible(new JSONArray().put(item("next-page")));
    assertEquals(0, selection.count());
  }

  @Test
  public void invalidPageDoesNotEraseValidSelection() throws Exception {
    AiReviewSelection selection = new AiReviewSelection();
    selection.replaceVisible(new JSONArray().put(item("kept")));
    selection.all(true);
    JSONArray oversized = new JSONArray();
    for (int i = 0; i < 101; i++) oversized.put(item("item-" + i));
    assertThrows(IllegalArgumentException.class, () -> selection.replaceVisible(oversized));
    assertThrows(
        IllegalArgumentException.class,
        () -> selection.replaceVisible(new JSONArray().put(new JSONObject())));
    assertEquals(1, selection.count());
  }

  @Test
  public void capturedSelectionRemainsStableUntilCopiedByWorker() throws Exception {
    AiReviewSelection selection = new AiReviewSelection();
    JSONObject original = item("original");
    selection.replaceVisible(AiReviewSelection.prepare(new JSONArray().put(original)));
    selection.all(true);
    AiReviewSelection.Snapshot captured = selection.capture();
    original.put("title", "外部修改");
    selection.replaceVisible(new JSONArray().put(item("next")));
    selection.all(true);
    JSONArray first = captured.copy();
    assertEquals("original", first.getJSONObject(0).getString("review_ref"));
    assertEquals("同名建议", first.getJSONObject(0).getString("title"));
    first.getJSONObject(0).put("title", "调用者修改");
    assertEquals("同名建议", captured.copy().getJSONObject(0).getString("title"));
    assertEquals("next", selection.snapshot().getJSONObject(0).getString("review_ref"));
  }
}
