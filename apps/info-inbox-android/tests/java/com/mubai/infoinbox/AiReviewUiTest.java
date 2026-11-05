package com.mubai.infoinbox;

import static org.junit.Assert.*;

import android.app.Application;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.CheckBox;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
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
    add(number, kind, System.currentTimeMillis());
  }

  private void add(int number, String kind, long sourceTime) throws Exception {
    JSONObject source =
        new JSONObject()
            .put("account_id", "fixture-owner")
            .put("talker", "fixture-room")
            .put("msg_id", number)
            .put("content", "合成资料")
            .put("created_at", sourceTime);
    source.put("evidence", new JSONArray().put(new JSONObject(source.toString())));
    JSONObject item =
        new JSONObject()
            .put("review_ref", "ai:" + AnalysisContract.hash("ui-" + number))
            .put("title", "合成建议 " + number)
            .put("kind", kind)
            .put("importance", "high")
            .put("reason", "界面验收")
            .put("details", "")
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

  @Test
  public void unconfirmedDeadlineMovesFromCachedCurrentToHistoryWhenTimePasses() throws Exception {
    long before=System.currentTimeMillis(),deadline=before+60000;
    add(501,"todo",before);
    try(android.database.Cursor row=memory.getReadableDatabase().rawQuery("SELECT ref,item_json FROM ai_reviews",null)) {
      assertTrue(row.moveToFirst());JSONObject item=new JSONObject(row.getString(1));
      item.put("due_at",java.time.Instant.ofEpochMilli(deadline).toString());
      ContentValues update=new ContentValues();update.put("item_json",item.toString());
      memory.getWritableDatabase().update("ai_reviews",update,"ref=?",new String[]{row.getString(0)});
    }
    add(502,"fact",before);
    open();assertNotNull(control("合成建议 501"));
    control("多选建议").performClick();control("全选本页").performClick();assertEquals(2,selected());
    java.lang.reflect.Method refresh=MainActivity.class.getDeclaredMethod("refreshAiSuggestions",long.class);
    refresh.setAccessible(true);refresh.invoke(activity,deadline+60000);settle();
    assertEquals(1,selected());assertNotNull(control("合成建议 502"));
    assertFalse(views().stream().anyMatch(v->v instanceof TextView&&((TextView)v).getText().toString().equals("合成建议 501")));
    java.lang.reflect.Field pagesField=MainActivity.class.getDeclaredField("aiSectionPages");pagesField.setAccessible(true);
    java.util.Map<?,?> pages=(java.util.Map<?,?>)pagesField.get(activity);
    Object history=pages.get("history");assertNotNull(history);
    java.lang.reflect.Field itemsField=history.getClass().getDeclaredField("items");itemsField.setAccessible(true);
    JSONArray archived=(JSONArray)itemsField.get(history);assertEquals(1,archived.length());
    assertEquals("合成建议 501",archived.getJSONObject(0).getString("title"));
    try(android.database.Cursor row=memory.getReadableDatabase().rawQuery("SELECT state FROM ai_reviews",null)) {
      assertTrue(row.moveToFirst());assertEquals("pending",row.getString(0));
    }
  }

  @Test
  public void largeMemoryPagesReadOffUiAndKeepNewestSearchAndPagination() throws Exception {
    for (int i = 0; i < 295; i++) {
      JSONObject source = new JSONObject().put("account_id", "fixture-owner").put("talker", "fixture-room")
          .put("msg_id", i).put("created_at", 1000).put("content", "原文 " + "长".repeat(16000));
      ContentValues value = new ContentValues();
      value.put("ref", "memory-" + i); value.put("title", "确认资料 " + i);
      value.put("kind", i % 2 == 0 ? "记忆" : "资料"); value.put("quote", "原文");
      value.put("source_json", source.toString()); value.put("created_at", i); value.put("updated_at", i);
      memory.getWritableDatabase().insertOrThrow("memories", null, value);
    }
    open();
    java.lang.reflect.Field readerField = MainActivity.class.getDeclaredField("memoryReads");
    readerField.setAccessible(true);
    java.util.concurrent.ExecutorService reader = (java.util.concurrent.ExecutorService) readerField.get(activity);
    java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1), release = new java.util.concurrent.CountDownLatch(1);
    reader.submit(() -> { entered.countDown(); try { release.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } });
    assertTrue(entered.await(2, java.util.concurrent.TimeUnit.SECONDS));
    try {
      java.lang.reflect.Method select = MainActivity.class.getDeclaredMethod("select", int.class); select.setAccessible(true); select.invoke(activity, 3);
      assertNotNull(control("正在读取资料…"));
      assertFalse(views().stream().anyMatch(v -> v instanceof TextView && ((TextView)v).getText().toString().equals("确认资料 294")));
    } finally { release.countDown(); }
    reader.submit(() -> {}).get(10, java.util.concurrent.TimeUnit.SECONDS);
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    assertNotNull(control("295 条资料 · 第 1/10 页"));
    assertEquals(30, views().stream().filter(v -> v instanceof TextView && ((TextView)v).getText().toString().matches("确认资料 \\d+")).count());
    control("下一页资料").performClick();
    reader.submit(() -> {}).get(10, java.util.concurrent.TimeUnit.SECONDS);
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    assertNotNull(control("295 条资料 · 第 2/10 页"));
    EditText search = (EditText) views().stream().filter(v -> "搜索确认资料".contentEquals(v.getContentDescription() == null ? "" : v.getContentDescription())).findFirst().get();
    search.setText("不存在"); search.setText("确认资料 294");
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(151));
    reader.submit(() -> {}).get(10, java.util.concurrent.TimeUnit.SECONDS);
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    assertNotNull(control("1 条资料 · 第 1/1 页"));
    assertNotNull(control("确认资料 294"));
    assertFalse(control("下一页资料").isEnabled());
    assertEquals(0, memory.memoryPage("%", "全部", 0, 30).getInt("total"));
    assertEquals(148, memory.memoryPage("", "记忆", 99, 30).getInt("total"));
  }

  @Test
  public void dialogEvidenceIsLazyPagedAndLongQuotesRemainComplete() throws Exception {
    open();
    JSONObject source = new JSONObject().put("content", "合成原文").put("created_at", 1000);
    JSONArray parts = new JSONArray();
    for (int i = 0; i < 30; i++) parts.put(new JSONObject().put("content", i == 0 ? "长".repeat(9000) + "末尾证据" : "原文编号 " + i).put("created_at", 1000));
    source.put("evidence", parts);
    LinearLayout root = new LinearLayout(activity); NativeUi.sources(activity, root, source);
    java.util.List<View> all = new java.util.ArrayList<>(); collect(root, all);
    assertTrue(all.size() < 10);
    Button expand = (Button)all.stream().filter(v -> v instanceof Button && ((Button)v).getText().toString().equals("展开完整原文")).findFirst().get();
    expand.performClick();
    all.clear(); collect(root, all);
    assertFalse(all.stream().anyMatch(v -> v instanceof TextView && ((TextView)v).getText().toString().equals("原文编号 5")));
    Button more = (Button)all.stream().filter(v -> v instanceof Button && v.getVisibility() == View.VISIBLE && ((Button)v).getText().toString().equals("继续展开这段原文")).findFirst().get();
    more.performClick(); more.performClick();
    all.clear(); collect(root, all);
    StringBuilder reconstructed = new StringBuilder();
    for(View v : all) if(v instanceof TextView && ((TextView)v).isTextSelectable()) reconstructed.append(((TextView)v).getText());
    assertTrue(reconstructed.toString().startsWith(parts.getJSONObject(0).getString("content")));
    Button next = (Button)all.stream().filter(v -> v instanceof Button && ((Button)v).getText().toString().equals("下一组原文")).findFirst().get();
    next.performClick(); all.clear(); collect(root, all);
    assertTrue(all.stream().anyMatch(v -> v instanceof TextView && ((TextView)v).getText().toString().equals("原文编号 5")));
    assertEquals(600, NativeUi.excerpt("长".repeat(599) + "😀长").length());
  }

  private void open() throws Exception {
    controller =
        Robolectric.buildActivity(
                MainActivity.class, new Intent(context, MainActivity.class).putExtra("tab", 1))
            .create()
            .start()
            .visible();
    activity = controller.get();
    settle();
  }

  private java.util.concurrent.ExecutorService worker() throws Exception {
    java.lang.reflect.Field field = MainActivity.class.getDeclaredField("worker");
    field.setAccessible(true);
    return (java.util.concurrent.ExecutorService) field.get(activity);
  }

  private void settle() throws Exception {
    for (int i = 0; i < 2; i++) {
      java.lang.reflect.Field writer = MainActivity.class.getDeclaredField("confirmations");
      writer.setAccessible(true);
      ((java.util.concurrent.ExecutorService)writer.get(activity)).submit(() -> {}).get(10, java.util.concurrent.TimeUnit.SECONDS);
      worker().submit(() -> {}).get(10, java.util.concurrent.TimeUnit.SECONDS);
      org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    }
  }

  private View proposalCard(String title) {
    View view = control(title);
    while (!view.isLongClickable()) view = (View) view.getParent();
    return view;
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

  private void section(String label) throws Exception {
    views().stream()
        .filter(v -> (label + "分区").equals(v.getContentDescription()))
        .findFirst()
        .orElseThrow(() -> new AssertionError("Missing section: " + label))
        .performClick();
    settle();
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
    settle();
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
    settle();
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
    settle();
    control("多选建议").performClick();
    control("全选本页").performClick();
    assertEquals(1, selected());
    control("取消全选").performClick();
    assertEquals(0, selected());
  }

  @Test
  public void singleReviewKeepsPartitionAndDirtyEditsUntilExplicitlyDiscarded() throws Exception {
    add(1, "memory");
    add(2, "reference");
    open();
    section("记忆");
    proposalCard("合成建议 1").performClick();
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    android.app.AlertDialog review = ShadowAlertDialog.getLatestAlertDialog();
    assertEquals("核对建议", org.robolectric.Shadows.shadowOf(review).getTitle().toString());
    // Inspect the editable content of the visible review.
    List<View> content = new ArrayList<>();
    collect(review.getWindow().getDecorView(), content);
    android.widget.EditText title =
        (android.widget.EditText)
            content.stream().filter(v -> v instanceof android.widget.EditText).findFirst().get();
    title.setText("修改中的内容");
    review.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).performClick();
    android.app.AlertDialog discard = ShadowAlertDialog.getLatestAlertDialog();
    assertTrue(review.isShowing());
    discard.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
    assertEquals("修改中的内容", title.getText().toString());
    review.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).performClick();
    ShadowAlertDialog.getLatestAlertDialog()
        .getButton(android.app.AlertDialog.BUTTON_NEGATIVE)
        .performClick();
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    assertFalse(review.isShowing());
    proposalCard("合成建议 1").performClick();
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    ShadowAlertDialog.getLatestAlertDialog()
        .getButton(android.app.AlertDialog.BUTTON_NEUTRAL)
        .performClick();
    settle();
    assertEquals(1, memory.suggestions().length());
    assertTrue(
        views().stream()
            .noneMatch(
                v ->
                    v instanceof TextView && ((TextView) v).getText().toString().equals("合成建议 2")));
  }

  @Test
  public void reviewActionsUseAvailableHeightWithoutOverlapping() throws Exception {
    add(1, "memory");
    JSONObject item = memory.suggestions().getJSONObject(0);
    String title = "需要完整核对的合成资料标题，需要完整核对的合成资料标题，需要完整核对的合成资料标题";
    item.put("title", title);
    ContentValues values = new ContentValues();
    values.put("title", title);
    values.put("item_json", item.toString());
    memory
        .getWritableDatabase()
        .update("ai_reviews", values, "ref=?", new String[] {item.getString("review_ref")});
    open();
    proposalCard(title).performClick();
    org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
    android.app.AlertDialog dialog = ShadowAlertDialog.getLatestAlertDialog();
    float density = activity.getResources().getDisplayMetrics().density;
    int width = Math.round(411 * density), height = Math.round(914 * density);
    View decor = dialog.getWindow().getDecorView();
    decor.measure(
        View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
        View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY));
    decor.layout(0, 0, width, height);
    android.widget.Button save = dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE);
    int[] location = new int[2];
    save.getLocationInWindow(location);
    assertTrue(
        "Save actions must stay at the bottom, not below a short form",
        location[1] + save.getHeight() >= height - Math.round(80 * density));
    android.widget.Button ignore = dialog.getButton(android.app.AlertDialog.BUTTON_NEUTRAL);
    android.widget.Button cancel = dialog.getButton(android.app.AlertDialog.BUTTON_NEGATIVE);
    int[] ignoreAt = new int[2], cancelAt = new int[2];
    ignore.getLocationInWindow(ignoreAt);
    cancel.getLocationInWindow(cancelAt);
    assertTrue(ignoreAt[0] + ignore.getWidth() <= cancelAt[0]);
    assertTrue(cancelAt[0] + cancel.getWidth() <= location[0]);
    assertTrue(location[0] + save.getWidth() <= width);
  }

  @Test
  public void fullConclusionIsVisibleAlongsideOriginalDialogue() throws Exception {
    add(1, "memory");
    String full = "本人原计划周五提交，后改为周一，目前仍待确认；不能当作已完成。";
    try (android.database.Cursor c =
        memory.getReadableDatabase().rawQuery("SELECT ref,item_json FROM ai_reviews", null)) {
      c.moveToFirst();
      JSONObject item = new JSONObject(c.getString(1)).put("details", full);
      ContentValues value = new ContentValues();
      value.put("item_json", item.toString());
      memory
          .getWritableDatabase()
          .update("ai_reviews", value, "ref=?", new String[] {c.getString(0)});
    }
    open();
    proposalCard("合成建议 1").performClick();
    android.app.AlertDialog dialog = ShadowAlertDialog.getLatestAlertDialog();
    List<View> controls = new ArrayList<>();
    java.util.ArrayDeque<View> pending = new java.util.ArrayDeque<>();
    pending.add(dialog.getWindow().getDecorView());
    while (!pending.isEmpty()) {
      View next = pending.remove();
      controls.add(next);
      if (next instanceof ViewGroup)
        for (int i = 0; i < ((ViewGroup) next).getChildCount(); i++)
          pending.add(((ViewGroup) next).getChildAt(i));
    }
    assertTrue(
        controls.stream()
            .anyMatch(
                v ->
                    v instanceof android.widget.EditText
                        && ((android.widget.EditText) v).getText().toString().equals(full)));
    dialog.getButton(android.content.DialogInterface.BUTTON_NEGATIVE).performClick();
    assertEquals(
        "合成资料",
        new JSONObject(memory.suggestions().getJSONObject(0).getJSONObject("source").toString())
            .getString("content"));
  }

  @Test
  public void longPressAndCancelReuseCardsWithoutReloading() throws Exception {
    add(1);
    open();
    View card = proposalCard("合成建议 1");
    assertTrue(card.performLongClick());
    assertSame(card, proposalCard("合成建议 1"));
    assertTrue(card.isSelected());
    assertEquals(1, selected());
    control("取消多选").performClick();
    assertSame(card, proposalCard("合成建议 1"));
    assertFalse(card.isSelected());
    assertEquals(0, selected());
    assertTrue(
        views().stream()
            .filter(v -> v instanceof CheckBox)
            .allMatch(v -> v.getVisibility() == View.INVISIBLE));
  }

  @Test
  public void historicalWorkHasItsOwnReviewSectionWithoutRewritingStoredEvidence() throws Exception {
    add(1);
    add(2, "todo", 1717570800000L);
    String before;
    try (android.database.Cursor c = memory.getReadableDatabase().rawQuery(
        "SELECT item_json FROM ai_reviews WHERE kind='todo'", null)) {
      assertTrue(c.moveToFirst());
      before = c.getString(0);
    }
    open();
    assertTrue(views().stream().noneMatch(v -> v instanceof TextView
        && ((TextView) v).getText().toString().equals("合成建议 2")));
    control("历史").performClick();
    settle();
    assertNotNull(proposalCard("合成建议 2"));
    JSONObject selected = AiSuggestionTypes.filter(memory.suggestions(), "history").getJSONObject(0);
    try {
      AiReviewBatch.preview(memory, new JSONArray().put(selected), "Asia/Shanghai");
      fail("Historical work must not be revived by a generic batch save");
    } catch (IllegalStateException expected) {
      assertTrue(expected.getMessage().contains("逐条核对"));
    }
    try {
      AiReviewBatch.confirm(memory, new JSONArray().put(new JSONObject()
          .put("proposal", selected).put("edited", new JSONObject())), System.currentTimeMillis());
      fail("A stale or forged preview cannot bypass historical review at commit");
    } catch (IllegalStateException expected) {
      assertTrue(expected.getMessage().contains("逐条核对"));
    }
    try (android.database.Cursor c = memory.getReadableDatabase().rawQuery(
        "SELECT item_json FROM ai_reviews WHERE kind='todo'", null)) {
      assertTrue(c.moveToFirst());
      assertEquals(before, c.getString(0));
    }
    assertEquals(0, new OrganizerStore(memory).workItems(true).length());
    assertEquals(1, AiReviewBatch.ignore(memory, new JSONArray().put(selected)));
  }

  @Test
  public void lateReadCannotReplaceSettingsAfterNavigation() throws Exception {
    add(1);
    open();
    java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1),
        release = new java.util.concurrent.CountDownLatch(1);
    worker()
        .submit(
            () -> {
              entered.countDown();
              try {
                release.await();
              } catch (InterruptedException failure) {
                Thread.currentThread().interrupt();
              }
            });
    assertTrue(entered.await(5, java.util.concurrent.TimeUnit.SECONDS));
    try {
      control("刷新整理结果").performClick();
      control("设置").performClick();
    } finally {
      release.countDown();
    }
    settle();
    assertTrue(
        views().stream()
            .noneMatch(
                v ->
                    v instanceof TextView && ((TextView) v).getText().toString().equals("合成建议 1")));
  }

  @Test
  public void sectionsSwitchWhileDatabaseWorkerIsBusyAndRefreshStillReloads() throws Exception {
    add(1);
    add(2, "todo", 1717570800000L);
    open();
    java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1),
        release = new java.util.concurrent.CountDownLatch(1);
    worker().submit(() -> {
      entered.countDown();
      try { release.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    });
    assertTrue(entered.await(5, java.util.concurrent.TimeUnit.SECONDS));
    try {
      long started = System.nanoTime();
      control("历史").performClick();
      assertNotNull(proposalCard("合成建议 2"));
      control("当前").performClick();
      assertNotNull(proposalCard("合成建议 1"));
      System.out.println("two_cached_section_switches_ms=" + (System.nanoTime() - started) / 1000000L);
      memory.reviewState(AiSuggestionTypes.filter(memory.suggestions(), "memory")
          .getJSONObject(0).getString("review_ref"), "ignored");
      control("刷新整理结果").performClick();
      assertNotNull(control("正在读取…"));
    } finally {
      release.countDown();
    }
    settle();
    assertTrue(views().stream().noneMatch(v -> v instanceof TextView
        && ((TextView) v).getText().toString().equals("合成建议 1")));
  }

  @Test
  public void sourceFooterSeparatesMetadataFromActionAtLargerFont() throws Exception {
    android.content.res.Configuration config =
        new android.content.res.Configuration(context.getResources().getConfiguration());
    config.fontScale = 1.3f;
    context.getResources().updateConfiguration(config, context.getResources().getDisplayMetrics());
    try {
      add(1);
      open();
      View action =
          views().stream()
              .filter(
                  v ->
                      v.getContentDescription() != null
                          && v.getContentDescription().toString().startsWith("查看建议出处："))
              .findFirst()
              .get();
      android.widget.LinearLayout footer = (android.widget.LinearLayout) action.getParent();
      float density = activity.getResources().getDisplayMetrics().density;
      int width = Math.round(320 * density);
      footer.measure(
          View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
          View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
      footer.layout(0, 0, width, footer.getMeasuredHeight());
      View metadata = footer.getChildAt(0);
      assertTrue(action.getLeft() - metadata.getRight() >= Math.round(16 * density));
      assertTrue(
          ((android.widget.LinearLayout.LayoutParams) footer.getLayoutParams()).topMargin
              >= Math.round(12 * density));
      assertTrue(action.getMeasuredHeight() >= Math.round(48 * density));
      assertTrue(metadata.getMeasuredWidth() > 0);
    } finally {
      config.fontScale = 1f;
      context
          .getResources()
          .updateConfiguration(config, context.getResources().getDisplayMetrics());
    }
  }

  @Test
  public void historyOrderHasAVisibleWayBackToRecentFirst() throws Exception {
    add(1);
    open();
    control("处理状态与更多操作").performClick();
    control("从最早历史接着整理").performClick();
    android.app.AlertDialog dialog = ShadowAlertDialog.getLatestAlertDialog();
    assertEquals("从最早尚未整理的消息开始？", org.robolectric.Shadows.shadowOf(dialog).getTitle().toString());
    dialog.dismiss();
    HistoryImport.request(context, true);
    control("刷新整理结果").performClick();
    settle();
    control("处理状态与更多操作").performClick();
    control("切回新消息优先").performClick();
    assertEquals(
        "切回新消息优先？",
        org.robolectric.Shadows.shadowOf(ShadowAlertDialog.getLatestAlertDialog())
            .getTitle()
            .toString());
    ShadowAlertDialog.getLatestAlertDialog().dismiss();
  }
  @Test
  public void confirmationIsPersistedWhileNetworkWorkerIsHeld() throws Exception {
    String base="https://queue.example.test",session=java.util.UUID.randomUUID().toString();
    context.getSharedPreferences("backend",Context.MODE_PRIVATE).edit().clear()
        .putString("base",base).putString("owner_session",session).commit();
    ActivityController<HarnessActivity> screen=Robolectric.buildActivity(HarnessActivity.class);
    HarnessActivity view=screen.get();
    java.lang.reflect.Field networkField=HarnessActivity.class.getDeclaredField("worker");
    networkField.setAccessible(true);
    java.util.concurrent.ExecutorService network=(java.util.concurrent.ExecutorService)networkField.get(view);
    java.util.concurrent.CountDownLatch held=new java.util.concurrent.CountDownLatch(1),release=new java.util.concurrent.CountDownLatch(1);
    network.submit(()->{held.countDown();try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}});
    assertTrue(held.await(5,java.util.concurrent.TimeUnit.SECONDS));
    try {
      screen.create().start().visible();
      JSONObject work=new JSONObject().put("work_item_id","work:queued").put("revision",1)
          .put("kind","todo").put("state","proposed").put("title","提交验收")
          .put("details","无需等待网络处理");
      JSONObject snapshot=new JSONObject().put("intents",new JSONArray()).put("review",new JSONArray())
          .put("work",new JSONArray().put(work)).put("activity",new JSONArray());
      java.lang.reflect.Method render=HarnessActivity.class.getDeclaredMethod("render",JSONObject.class,JSONArray.class,boolean.class);
      render.setAccessible(true);render.invoke(view,snapshot,new JSONArray(),true);
      List<View> elements=new ArrayList<>();collect(view.getWindow().getDecorView(),elements);
      View confirm=elements.stream().filter(v->v instanceof TextView&&((TextView)v).getText().toString().equals("确认")).findFirst().get();
      confirm.performClick();confirm.performClick();
      java.lang.reflect.Field localField=HarnessActivity.class.getDeclaredField("submissions");
      localField.setAccessible(true);
      ((java.util.concurrent.ExecutorService)localField.get(view)).submit(()->{}).get(5,java.util.concurrent.TimeUnit.SECONDS);
      org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      JSONArray decisions=RuntimeActions.list(new KnowledgeStore(memory,base));
      assertEquals(1,decisions.length());
      assertEquals("confirm",decisions.getJSONObject(0).getJSONObject("payload").getString("action"));
      elements.clear();collect(view.getWindow().getDecorView(),elements);
      assertTrue(elements.stream().anyMatch(v->v instanceof TextView&&((TextView)v).getText().toString().contains("等待宁宁接收")));
      View submitted=elements.stream().filter(v->v instanceof TextView&&((TextView)v).getText().toString().equals("确认")).findFirst().get();
      assertFalse(submitted.isEnabled());
    } finally {
      screen.destroy();release.countDown();
      context.getSharedPreferences("backend",Context.MODE_PRIVATE).edit().clear().commit();
    }
  }

  @Test
  public void manualMemoryConfirmationDoesNotWaitForTheReadWorker() throws Exception {
    add(920);open();
    JSONObject source=memory.suggestions().getJSONObject(0).getJSONObject("source");
    java.util.concurrent.CountDownLatch entered=new java.util.concurrent.CountDownLatch(1),release=new java.util.concurrent.CountDownLatch(1);
    worker().submit(()->{entered.countDown();try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}});
    assertTrue(entered.await(5,java.util.concurrent.TimeUnit.SECONDS));
    try {
      java.lang.reflect.Method show=MainActivity.class.getDeclaredMethod("saveDialog",JSONObject.class);
      show.setAccessible(true);show.invoke(activity,source);
      org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      android.app.AlertDialog dialog=ShadowAlertDialog.getLatestAlertDialog();
      dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
      java.lang.reflect.Field field=MainActivity.class.getDeclaredField("confirmations");field.setAccessible(true);
      ((java.util.concurrent.ExecutorService)field.get(activity)).submit(()->{}).get(5,java.util.concurrent.TimeUnit.SECONDS);
      org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      assertFalse(dialog.isShowing());
      assertEquals(1,memory.list().length());
      JSONObject proposal = memory.suggestions().getJSONObject(0);
      java.lang.reflect.Method review = MainActivity.class.getDeclaredMethod("aiDialog", JSONObject.class);
      review.setAccessible(true); review.invoke(activity, proposal);
      org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      android.app.AlertDialog suggestion = ShadowAlertDialog.getLatestAlertDialog();
      suggestion.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
      ((java.util.concurrent.ExecutorService)field.get(activity)).submit(() -> {}).get(5,java.util.concurrent.TimeUnit.SECONDS);
      org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      assertFalse(suggestion.isShowing());
      assertEquals(proposal.getString("title"), memory.list().getJSONObject(0).getString("title"));
    }finally{release.countDown();}
  }

}
