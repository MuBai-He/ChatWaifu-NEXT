package com.mubai.infoinbox;

import static org.junit.Assert.*;

import android.app.Application;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.CheckBox;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowAlertDialog;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 33, application = Application.class)
public class AiReviewUiTest {
  private Context context;
  private MemoryStore memory;
  private ActivityController<MainActivity> controller;
  private MainActivity activity;

  @Before
  public void setup() {
    context = RuntimeEnvironment.getApplication();
    context.deleteDatabase("information.db");
    memory = new MemoryStore(context);
  }

  @After
  public void cleanup() {
    if (controller != null) controller.destroy();
    memory.close();
    context.deleteDatabase("information.db");
  }

  private void add(int number) throws Exception {
    add(number, "fact");
  }

  private void add(int number, String kind) throws Exception {
    JSONObject source =
        new JSONObject()
            .put("account_id", "fixture-owner")
            .put("talker", "fixture-room")
            .put("msg_id", number)
            .put("content", "合成资料")
            .put("created_at", 1791800000000L);
    source.put("evidence", new JSONArray().put(new JSONObject(source.toString())));
    JSONObject item =
        new JSONObject()
            .put("review_ref", "ai:" + AnalysisContract.hash("ui-" + number))
            .put("title", "合成建议 " + number)
            .put("kind", kind)
            .put("importance", "high")
            .put("reason", "界面验收")
            .put("source", source);
    ContentValues values = new ContentValues();
    values.put("ref", item.getString("review_ref"));
    values.put("title", item.getString("title"));
    values.put("kind", kind);
    values.put("importance", "high");
    values.put("reason", "界面验收");
    values.put("item_json", item.toString());
    values.put("created_at", 1791800000000L + number);
    memory.getWritableDatabase().insertOrThrow("ai_reviews", null, values);
  }

  private void open() {
    controller =
        Robolectric.buildActivity(
                MainActivity.class, new Intent(context, MainActivity.class).putExtra("tab", 1))
            .create()
            .start()
            .visible();
    activity = controller.get();
  }

  private List<View> views() {
    List<View> result = new ArrayList<>();
    collect(activity.getWindow().getDecorView(), result);
    return result;
  }

  private void collect(View view, List<View> result) {
    result.add(view);
    if (view instanceof ViewGroup)
      for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++)
        collect(((ViewGroup) view).getChildAt(i), result);
  }

  private View control(String text) {
    return views().stream()
        .filter(v -> v instanceof TextView && ((TextView) v).getText().toString().equals(text))
        .findFirst()
        .orElseThrow(() -> new AssertionError("Missing control: " + text));
  }

  private long selected() {
    return views().stream()
        .filter(v -> v instanceof CheckBox && ((CheckBox) v).isChecked())
        .count();
  }

  private void section(String label) {
    views().stream()
        .filter(v -> (label + "分区").equals(v.getContentDescription()))
        .findFirst()
        .orElseThrow(() -> new AssertionError("Missing section: " + label))
        .performClick();
  }

  @Test
  public void typeBadgesAndPartitionSelectionKeepOtherKindsOutOfBatch() throws Exception {
    add(1, "memory");
    add(2, "decision");
    add(3, "reference");
    add(4, "todo");
    add(5, "event");
    open();
    for (String label : new String[] {"记忆", "记忆 · 决定", "资料", "事项 · 待办", "事项 · 日程"})
      assertTrue(
          "Missing card type: " + label,
          views().stream().anyMatch(v -> ("建议类型：" + label).equals(v.getContentDescription())));
    section("记忆");
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(2, selected());
    section("资料");
    assertEquals(0, selected());
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(1, selected());
    section("事项");
    assertEquals(0, selected());
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(2, selected());
    assertEquals(5, memory.suggestions().length());
  }

  @Test
  public void refreshingPartitionKeepsItsSelectionAndDoesNotSelectNewMemory() throws Exception {
    add(1, "memory");
    add(2, "reference");
    open();
    section("记忆");
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(1, selected());
    add(3, "todo");
    add(4, "memory");
    control("刷新整理结果").performClick();
    assertEquals(1, selected());
    assertEquals(2, views().stream().filter(v -> v instanceof CheckBox).count());
  }

  @Test
  public void manualRefreshRetainsSelectionWithoutSelectingNewSuggestion() throws Exception {
    for (int i = 1; i <= 3; i++) add(i);
    open();
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(3, selected());
    add(4);
    control("刷新整理结果").performClick();
    assertEquals(3, selected());
    CheckBox incoming =
        (CheckBox)
            views().stream()
                .filter(
                    v ->
                        v instanceof CheckBox
                            && "选择建议 合成建议 4".contentEquals(v.getContentDescription()))
                .findFirst()
                .get();
    assertFalse(incoming.isChecked());
    control("批量忽略").performClick();
    ShadowAlertDialog.getLatestAlertDialog()
        .getButton(android.app.AlertDialog.BUTTON_NEGATIVE)
        .performClick();
    assertEquals(4, memory.suggestions().length());
    control("取消多选").performClick();
    assertEquals(0, selected());
  }

  @Test
  public void selectAllAndNavigationStayWithinHundredSuggestionPage() throws Exception {
    for (int i = 1; i <= 101; i++) add(i);
    open();
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(100, selected());
    control("下一页建议").performClick();
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(1, selected());
    control("取消全选").performClick();
    assertEquals(0, selected());
  }
}
