package com.mubai.infoinbox;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.widget.*;
import java.time.ZoneId;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONArray;
import org.json.JSONObject;

/** Confirmed work and reminder UI; all changes retain their source and revision. */
public final class OrganizerActivity extends Activity {
  private static final int BG = 0xFFF7F7FB,
      INK = 0xFF242338,
      MUTED = 0xFF6B6A7F,
      PURPLE = 0xFF6152A5;
  private final ExecutorService worker = Executors.newSingleThreadExecutor();
  private final ExecutorService confirmations = Executors.newSingleThreadExecutor();
  private final Handler main = new Handler(Looper.getMainLooper());
  private MemoryStore memory;
  private OrganizerStore store;
  private LinearLayout body;
  private volatile int generation;
  private boolean closed = false;

  private interface Task {
    Object run() throws Exception;
  }

  private interface Done {
    void accept(Object result) throws Exception;
  }

  @Override
  public void onCreate(Bundle saved) {
    super.onCreate(saved);
    memory = new MemoryStore(this);
    store = new OrganizerStore(memory);
    LinearLayout outer = column();
    outer.setBackgroundColor(BG);
    outer.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              0, insets.getSystemWindowInsetTop(), 0, insets.getSystemWindowInsetBottom());
          return insets;
        });
    LinearLayout header = column();
    header.setPadding(dp(22), dp(18), dp(22), dp(12));
    header.addView(text("今日事项", 26, INK, true));
    header.addView(text("知道要做什么，也看得见消息出处", 14, MUTED, false));
    outer.addView(header);
    ScrollView scroll = new ScrollView(this);
    scroll.setFillViewport(true);
    body = column();
    body.setPadding(dp(18), dp(4), dp(18), dp(20));
    scroll.addView(body);
    outer.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    LinearLayout nav = new LinearLayout(this);
    nav.setPadding(dp(8), dp(6), dp(8), dp(6));
    nav.setBackgroundColor(0xFFFFFFFF);
    String[] names = NativeUi.NAV_LABELS;
    for (int i = 0; i < names.length; i++) {
      int index = i;
      TextView item = text(names[i], 14, i == 2 ? PURPLE : MUTED, i == 2);
      item.setGravity(Gravity.CENTER);
      item.setBackground(shape(i == 2 ? 0xFFF0EDF8 : 0xFFFFFFFF, 12));
      item.setContentDescription(names[i]);
      item.setOnClickListener(
          v -> {
            if (index != 2) {
              startActivity(
                  new Intent(this, MainActivity.class)
                      .putExtra("tab", index)
                      .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP));
              finish();
            }
          });
      LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(50), 1);
      lp.setMargins(dp(3), 0, dp(3), 0);
      nav.addView(item, lp);
    }
    outer.addView(nav);
    setContentView(outer);
    render();
    if (getIntent().hasExtra("proposal"))
      try {
        form(new JSONObject(getIntent().getStringExtra("proposal")));
      } catch (Exception e) {
        toast("建议无法读取，请刷新收件箱");
      }
    if (getIntent().hasExtra("work_id"))
      try {
        JSONObject item = store.work(getIntent().getStringExtra("work_id"));
        if (item != null) detail(item);
      } catch (Exception e) {
        toast("事项无法读取");
      }
  }

  @Override
  public void onNewIntent(Intent intent) {
    super.onNewIntent(intent);
    setIntent(intent);
    render();
    try {
      JSONObject item = store.work(intent.getStringExtra("work_id"));
      if (item != null) detail(item);
    } catch (Exception ignored) {
    }
  }

  @Override
  public void onResume() {
    super.onResume();
    worker.execute(
        () -> {
          try {
            ReminderScheduler.reconcile(this);
          } catch (Exception ignored) {
          }
        });
  }

  @Override
  public void onDestroy() {
    generation++;
    worker.shutdownNow();
    confirmations.shutdown();
    main.removeCallbacksAndMessages(null);
    super.onDestroy();
  }

  private int dp(int n) {
    return Math.round(n * getResources().getDisplayMetrics().density);
  }

  private LinearLayout column() {
    LinearLayout l = new LinearLayout(this);
    l.setOrientation(LinearLayout.VERTICAL);
    return l;
  }

  private GradientDrawable shape(int color, int radius) {
    GradientDrawable d = new GradientDrawable();
    d.setColor(color);
    d.setCornerRadius(dp(radius));
    return d;
  }

  private TextView text(String value, int size, int color, boolean bold) {
    return NativeUi.text(this, value, size, color, bold);
  }

  private void gap(LinearLayout view, int size) {
    view.addView(new android.view.View(this), new LinearLayout.LayoutParams(1, dp(size)));
  }

  private LinearLayout card() {
    LinearLayout c = column();
    c.setPadding(dp(18), dp(18), dp(18), dp(18));
    c.setBackground(shape(0xFFFFFFFF, 18));
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
    p.setMargins(0, 0, 0, dp(12));
    body.addView(c, p);
    return c;
  }

  private void button(LinearLayout view, String label, Runnable click) {
    Button b = new Button(this);
    b.setText(label);
    b.setAllCaps(false);
    b.setTextColor(PURPLE);
    b.setBackground(shape(0xFFF0EDF8, 12));
    NativeUi.action(b);
    b.setOnClickListener(v -> click.run());
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
    p.setMargins(0, dp(8), 0, 0);
    view.addView(b, p);
  }

  private void toast(String value) {
    Toast.makeText(this, value, Toast.LENGTH_LONG).show();
  }

  private void async(Task task, Done done) {
    int token = generation;
    worker.execute(
        () -> {
          if (token != generation) return;
          try {
            Object result = task.run();
            main.post(
                () -> {
                  if (token != generation || isFinishing()) return;
                  try {
                    done.accept(result);
                  } catch (Exception e) {
                    toast(e.getMessage());
                  }
                });
          } catch (Exception e) {
            main.post(
                () -> {
                  if (token == generation && !isFinishing())
                    toast(e.getMessage() == null ? "操作未完成，请重试" : e.getMessage());
                });
          }
        });
  }

  private static String label(String state) {
    switch (state) {
      case "open":
        return "待执行";
      case "waiting":
        return "等待别人";
      case "blocked":
        return "有阻塞";
      case "completed":
        return "已完成";
      case "cancelled":
        return "已取消";
      case "posted":
        return "已发出通知";
      case "posting":
      case "uncertain":
        return "通知结果待核对";
      case "missed":
        return "提醒已过有效窗口";
      case "blocked_notification":
        return "未获通知权限";
      default:
        return state;
    }
  }

  private void render() {
    generation++;
    body.removeAllViews();
    LinearLayout controls = card();
    Button refresh = NativeUi.button(this, "刷新", this::render, false);
    Button ended =
        NativeUi.button(
            this,
            closed ? "查看当前事项" : "已完成和取消",
            () -> {
              closed = !closed;
              render();
            },
            false);
    NativeUi.actions(this, controls, refresh, ended);
    LinearLayout more = NativeUi.disclosure(this, controls, "提醒与更多操作");
    more.addView(text("截止时间、日程和提醒分别保存；关闭提示不会完成任务。", 13, MUTED, false));
    button(more, "通知与提醒权限", this::permissions);
    LinearLayout pending = card();
    pending.addView(text("正在读取整理进度…", 13, MUTED, false));
    TextView loading = text("正在读取事项…", 14, MUTED, false);
    body.addView(loading);
    async(
        () -> {
          JSONArray all = store.workItems(closed);
          JSONObject data =
              new JSONObject().put("items", all);
          return data.put(
              "brief",
              DailyBrief.build(all, new ApiSettings(this).timezone(), System.currentTimeMillis()));
        },
        value -> {
          JSONObject data = (JSONObject) value;
          body.removeView(loading);
          JSONArray items = data.getJSONArray("items");
          if (closed) {
            JSONArray done = new JSONArray();
            for (int i = 0; i < items.length(); i++)
              if (items.getJSONObject(i).getString("state").matches("completed|cancelled"))
                done.put(items.getJSONObject(i));
            section("已结束", done);
          } else {
            JSONObject brief = data.getJSONObject("brief");
            section("今天", brief.getJSONArray("today"));
            section("已过日期，可能需要回看", brief.getJSONArray("past_due"));
            section("等待与阻塞", brief.getJSONArray("waiting"));
            section("尚未定时间", brief.getJSONArray("undated"));
            JSONArray later = new JSONArray();
            long end =
                java.time.LocalDate.now(ZoneId.of(new ApiSettings(this).timezone()))
                    .plusDays(1)
                    .atStartOfDay(ZoneId.of(new ApiSettings(this).timezone()))
                    .toInstant()
                    .toEpochMilli();
            for (int i = 0; i < items.length(); i++) {
              JSONObject item = items.getJSONObject(i);
              Long at =
                  OrganizerStore.nullable(
                      item, item.getString("kind").equals("event") ? "starts_at" : "due_at");
              if (at != null && at >= end) later.put(item);
            }
            section("接下来的安排", later);
          }
          if (items.length() == 0) {
            LinearLayout empty = card();
            empty.addView(text("从一条消息建立事项", 18, INK, true));
            gap(empty, 8);
            empty.addView(text("在消息出处中选择建立待办或日程，也可以确认 AI 的整理建议。", 14, MUTED, false));
          }
        });
    async(
        () -> new JSONObject().put("queue", store.queueStats()).put("reviews", memory.suggestions().length()),
        value -> {
          JSONObject data = (JSONObject) value;
          JSONObject q = data.getJSONObject("queue");
          pending.removeAllViews();
          pending.addView(text(data.getInt("reviews") + " 条建议待确认", 17, PURPLE, true));
          gap(pending, 6);
          pending.addView(
              text(
                  "待整理 "
                      + q.optInt("pending")
                      + " 段 · 整理中 "
                      + q.optInt("leased")
                      + " 段 · 失败待重试 "
                      + q.optInt("failed")
                      + " 段",
                  13,
                  MUTED,
                  false));
          button(
              pending,
              "打开收件箱",
              () -> {
                startActivity(
                    new Intent(this, MainActivity.class)
                        .putExtra("tab", 1)
                        .addFlags(
                            Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP));
                finish();
              });
          if (q.optInt("failed") > 0)
            button(
                pending,
                "重新尝试失败的资料",
                () ->
                    async(
                        () -> {
                          store.retryFailed();
                          AnalysisJob.enqueue(this, 1000L);
                          return null;
                        },
                        r -> render()));
        });
  }

  private void section(String heading, JSONArray items) throws Exception {
    if (items.length() == 0) return;
    LinearLayout h = card();
    h.addView(text(heading + " · " + items.length(), 17, INK, true));
    for (int i = 0; i < items.length(); i++) {
      JSONObject item = items.getJSONObject(i);
      LinearLayout c = card();
      c.addView(text(item.getString("title"), 17, INK, true));
      gap(c, 7);
      String involvement = item.getString("involvement");
      c.addView(
          text(
              (item.getString("kind").equals("event") ? "日程" : "待办")
                  + " · "
                  + label(item.getString("state"))
                  + " · "
                  + (involvement.equals("self")
                      ? "本人"
                      : involvement.equals("other") ? "其他人" : "主体待核对"),
              12,
              PURPLE,
              false));
      Long at =
          OrganizerStore.nullable(
              item, item.getString("kind").equals("event") ? "starts_at" : "due_at");
      if (at != null) {
        gap(c, 6);
        c.addView(
            text(
                TimeRules.display(at, item.getString("timezone"))
                    + " · "
                    + item.getString("timezone"),
                13,
                MUTED,
                false));
      }
      c.setOnClickListener(v -> detail(item));
    }
  }

  private void sourceViews(LinearLayout view, JSONObject source) throws Exception {
    NativeUi.sources(this, view, source);
  }

  private void detail(JSONObject item) {
    try {
      LinearLayout view = column();
      view.setPadding(dp(20), dp(8), dp(20), dp(16));
      view.addView(text(item.getString("title"), 19, INK, true));
      gap(view, 8);
      view.addView(text(label(item.getString("state")), 14, PURPLE, true));
      if (!item.optString("location").isEmpty())
        view.addView(text("地点：" + item.optString("location"), 14, INK, false));
      if (!item.optString("details").isEmpty())
        view.addView(text(item.optString("details"), 14, MUTED, false));
      for (String key : new String[] {"due_at", "starts_at", "ends_at"})
        if (!item.isNull(key)) {
          gap(view, 6);
          view.addView(
              text(
                  (key.equals("due_at") ? "截止：" : key.equals("starts_at") ? "开始：" : "结束：")
                      + TimeRules.display(item.getLong(key), item.getString("timezone")),
                  14,
                  INK,
                  false));
        }
      JSONObject rule = store.reminder(item.getString("id"));
      if (rule != null) {
        gap(view, 8);
        String state = rule.getString("state");
        view.addView(
            text(
                "提醒："
                    + TimeRules.display(rule.getLong("due_at"), item.getString("timezone"))
                    + " · "
                    + (state.equals("scheduled")
                        ? "已安排"
                        : state.equals("blocked") ? "通知权限未开启" : label(state)),
                13,
                MUTED,
                false));
      }
      gap(view, 14);
      view.addView(text("消息出处", 16, INK, true));
      sourceViews(view, new JSONObject(item.getString("source_json")));
      JSONArray changes = store.changes(item.getString("id"));
      gap(view, 14);
      view.addView(text("变更记录", 16, INK, true));
      for (int i = 0; i < changes.length(); i++) {
        JSONObject change = changes.getJSONObject(i);
        gap(view, 5);
        String operation = change.getString("operation");
        view.addView(
            text(
                "第 "
                    + change.getInt("revision")
                    + " 次 · "
                    + operationLabel(operation)
                    + " · "
                    + TimeRules.display(change.getLong("created_at"), item.getString("timezone")),
                12,
                MUTED,
                false));
      }
      ScrollView scroll = new ScrollView(this);
      scroll.addView(view);
      new AlertDialog.Builder(this)
          .setTitle("已确认事项")
          .setView(scroll)
          .setNegativeButton("关闭", null)
          .setPositiveButton(
              "编辑",
              (d, w) -> {
                try {
                  form(store.editProposal(item));
                } catch (Exception e) {
                  toast(e.getMessage());
                }
              })
          .setNeutralButton("改变状态", (d, w) -> stateMenu(item))
          .show();
    } catch (Exception e) {
      toast("读取事项失败，请刷新");
    }
  }

  private static String operationLabel(String operation) {
    if (operation.equals("create")) return "建立";
    if (operation.equals("update")) return "修改";
    if (operation.equals("complete")) return "完成";
    if (operation.equals("cancel")) return "取消";
    return label(operation);
  }

  private void stateMenu(JSONObject item) {
    boolean ended = item.optString("state").matches("completed|cancelled");
    String[] names = ended ? new String[] {"重新打开"} : new String[] {"标记完成", "等待别人", "有阻塞", "取消事项"};
    String[] states =
        ended
            ? new String[] {"open"}
            : new String[] {"completed", "waiting", "blocked", "cancelled"};
    new AlertDialog.Builder(this)
        .setTitle("更新事项状态")
        .setItems(
            names,
            (d, index) ->
                async(
                    () -> {
                      ReminderScheduler.changeState(this, memory, item, states[index]);
                      return null;
                    },
                    r -> {
                      toast("事项状态已更新");
                      render();
                    }))
        .show();
  }

  private void form(JSONObject proposal) {
    new SuggestionReview(this, memory, confirmations, this::render).showWork(proposal);
  }

  private void permissions() {
    String status =
        "系统通知："
            + (ReminderScheduler.notificationsAllowed(this) ? "已开启" : "未开启")
            + "\n准时提醒："
            + (ReminderScheduler.preciseAllowed(this) ? "已授权" : "普通提醒，可能延后");
    new AlertDialog.Builder(this)
        .setTitle("通知与提醒")
        .setMessage(status + "\n\n你可以开启通知，并授权按指定时间提醒。事项会保留在本机。")
        .setPositiveButton(
            "开启通知",
            (d, w) -> {
              if (Build.VERSION.SDK_INT >= 33)
                requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 71);
              else
                startActivity(
                    new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
            })
        .setNeutralButton(
            "准时提醒",
            (d, w) -> {
              if (Build.VERSION.SDK_INT >= 31)
                startActivity(
                    new Intent(
                        Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                        Uri.parse("package:" + getPackageName())));
              else toast("当前系统无需单独授权");
            })
        .setNegativeButton("关闭", null)
        .show();
  }
}
