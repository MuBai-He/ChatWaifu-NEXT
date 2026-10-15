package com.mubai.infoinbox;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONArray;
import org.json.JSONObject;

public final class MainActivity extends Activity {
  private static final int BG = 0xFFF7F7FB,
      INK = 0xFF242338,
      MUTED = 0xFF6B6A7F,
      PURPLE = 0xFF6152A5,
      GREEN = 0xFF23795D;
  private final Handler main = new Handler(Looper.getMainLooper());
  private final ExecutorService worker = Executors.newSingleThreadExecutor();
  private RootGateway root;
  private MemoryStore memories;
  private LinearLayout outer, body, nav;
  private TextView heading, subtitle, importStatus;
  private Button importAction;
  private TextView freshLabel, messageCount, conversationCount;
  private boolean dashboardReady = false, dashboardRunning = false;
  private int tab = 0, generation = 0;
  private boolean busy = false, resumed = false, conversationMode = false;
  private boolean excludedInboxMode = false;
  private String conversationSearch = "";
  private final ConversationSelection selection = new ConversationSelection();
  private final java.util.Map<String, CheckBox> selectionBoxes = new java.util.LinkedHashMap<>();
  private boolean multiSelectMode = false, bulkExcludeAllowed = false, bulkRestoreAllowed = false;
  private LinearLayout bulkBar;
  private TextView bulkCount;
  private Button bulkAll, bulkExclude, bulkRestore;
  private Runnable redrawRooms, refreshRooms;
  private boolean aiPageVisible = false;
  private JSONObject aiSourceFilter;
  private String aiPageFingerprint = "";
  private final AiReviewSelection aiSelection = new AiReviewSelection();
  private final java.util.Map<String, CheckBox> aiSelectionBoxes = new java.util.LinkedHashMap<>();
  private boolean aiMultiSelectMode = false;
  private int aiPageIndex = 0;
  private String aiSection = "all";
  private LinearLayout aiBulkBar;
  private TextView aiBulkCount;
  private Button aiBulkAll, aiBulkIgnore, aiBulkSave, aiBulkCancel;
  private final Runnable pulse =
      new Runnable() {
        @Override
        public void run() {
          if (resumed && tab == 0 && !busy && dashboardReady) refreshHomeStats();
          if (resumed && importStatus != null)
            importStatus.setText(HistoryImport.status(MainActivity.this));
          if (resumed && importAction != null)
            importAction.setText(
                HistoryImport.requested(MainActivity.this) ? "暂停全量处理" : "开始或继续全量处理");
          if (resumed && aiPageVisible && !busy && hasWindowFocus()) refreshAiSuggestions();
          if (resumed) main.postDelayed(this, 15000);
        }
      };

  private interface Task {
    Object run() throws Exception;
  }

  private interface Done {
    void accept(Object result) throws Exception;
  }

  @Override
  public void onCreate(Bundle saved) {
    super.onCreate(saved);
    root = new RootGateway(this);
    memories = new MemoryStore(this);
    outer = new LinearLayout(this);
    outer.setOrientation(LinearLayout.VERTICAL);
    outer.setBackgroundColor(BG);
    outer.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              0, insets.getSystemWindowInsetTop(), 0, insets.getSystemWindowInsetBottom());
          return insets;
        });
    LinearLayout header = column();
    header.setPadding(dp(22), dp(18), dp(22), dp(12));
    heading = text("信息收件箱", 26, INK, true);
    subtitle = text("让重要的信息，有出处、有去向", 14, MUTED, false);
    header.addView(heading);
    header.addView(subtitle);
    outer.addView(header);
    ScrollView scroll = new ScrollView(this);
    scroll.setFillViewport(true);
    body = column();
    body.setPadding(dp(18), dp(4), dp(18), dp(20));
    scroll.addView(body);
    outer.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    nav = new LinearLayout(this);
    nav.setPadding(dp(8), dp(6), dp(8), dp(6));
    nav.setBackgroundColor(Color.WHITE);
    outer.addView(nav);
    setContentView(outer);
    select(getIntent().getIntExtra("tab", 0));
    AnalysisJob.schedule(this, new ApiSettings(this).automatic());
    AnalysisJob.enqueueIntake(this, 1000L);
  }

  @Override
  public void onResume() {
    super.onResume();
    try {
      HistoryImportService.resume(this);
    } catch (RuntimeException unavailable) {
      HistoryImport.pause(this, "系统暂未允许继续传输，请点击继续");
    }
    KnowledgeJob.enqueue(this);
    resumed = true;
    main.postDelayed(pulse, 15000);
  }

  @Override
  public void onPause() {
    resumed = false;
    main.removeCallbacks(pulse);
    super.onPause();
  }

  @Override
  public void onDestroy() {
    generation++;
    main.removeCallbacksAndMessages(null);
    worker.shutdownNow();
    super.onDestroy();
  }

  private int dp(float n) {
    return Math.round(n * getResources().getDisplayMetrics().density);
  }

  private LinearLayout column() {
    LinearLayout l = new LinearLayout(this);
    l.setOrientation(LinearLayout.VERTICAL);
    return l;
  }

  private TextView text(String s, int size, int color, boolean bold) {
    TextView v = new TextView(this);
    v.setText(s);
    v.setTextSize(size);
    v.setTextColor(color);
    v.setLineSpacing(dp(3), 1);
    if (bold) v.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
    return v;
  }

  private GradientDrawable shape(int color, int radius) {
    GradientDrawable d = new GradientDrawable();
    d.setColor(color);
    d.setCornerRadius(dp(radius));
    return d;
  }

  private LinearLayout card() {
    LinearLayout v = column();
    v.setPadding(dp(18), dp(18), dp(18), dp(18));
    v.setBackground(shape(Color.WHITE, 18));
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
    p.setMargins(0, 0, 0, dp(12));
    body.addView(v, p);
    return v;
  }

  private void gap(LinearLayout l, int n) {
    View v = new View(this);
    l.addView(v, new LinearLayout.LayoutParams(1, dp(n)));
  }

  private Button button(String title, Runnable action, boolean primary) {
    Button b = new Button(this);
    b.setText(title);
    b.setTextSize(14);
    b.setAllCaps(false);
    b.setMinHeight(dp(48));
    b.setTextColor(primary ? Color.WHITE : PURPLE);
    b.setBackground(shape(primary ? PURPLE : 0xFFF0EDF8, 12));
    NativeUi.action(b);
    b.setOnClickListener(v -> action.run());
    return b;
  }

  private Button addButton(LinearLayout l, String title, Runnable action, boolean primary) {
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
    p.setMargins(0, dp(8), 0, 0);
    Button item = button(title, action, primary);
    l.addView(item, p);
    return item;
  }

  private String date(long millis) {
    return millis <= 0 ? "尚无记录" : TimeRules.display(millis, new ApiSettings(this).timezone());
  }

  private String display(JSONObject m) {
    String value = MessagePresentation.body(m);
    if (m.optInt("type", 1) != 1) {
      String kind =
          m.optInt("type") == 3
              ? "图片"
              : m.optInt("type") == 34 ? "语音" : m.optInt("type") == 43 ? "视频" : "非文本消息";
      return "[" + kind + "]\n" + value;
    }
    return value;
  }

  @Override
  public void onNewIntent(Intent intent) {
    super.onNewIntent(intent);
    setIntent(intent);
    select(intent.getIntExtra("tab", 0));
  }

  private void select(int index) {
    if (index == 1) {
      aiInbox();
      return;
    }
    if (index == 2) {
      startActivity(new Intent(this, OrganizerActivity.class));
      return;
    }
    tab = index;
    if (index == 1) excludedInboxMode = false;
    generation++;
    busy = false;
    renderNav();
    if (index == 0) home();
    else if (index == 1) inbox();
    else if (index == 3) memoryPage();
    else settings();
  }

  private void renderNav() {
    nav.removeAllViews();
    String[] names = {"概览", "AI建议", "事项", "记忆", "设置"};
    for (int i = 0; i < names.length; i++) {
      final int next = i;
      TextView item = text(names[i], 14, i == tab ? PURPLE : MUTED, i == tab);
      item.setGravity(Gravity.CENTER);
      item.setMinHeight(dp(50));
      item.setContentDescription(names[i]);
      item.setBackground(shape(i == tab ? 0xFFF0EDF8 : Color.WHITE, 12));
      item.setOnClickListener(v -> select(next));
      LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(50), 1);
      p.setMargins(dp(3), 0, dp(3), 0);
      nav.addView(item, p);
    }
  }

  private void page(String title, String hint) {
    resetSelectionPage();
    resetAiSelectionPage();
    importStatus = null;
    importAction = null;
    generation++;
    dashboardReady = false;
    aiPageVisible = false;
    body.removeAllViews();
    heading.setText(title);
    subtitle.setText(hint);
  }

  private void async(Task task, Done done) {
    int token = ++generation;
    busy = true;
    updateBulkBar();
    updateAiBulkBar();
    worker.execute(
        () -> {
          try {
            Object result = task.run();
            main.post(
                () -> {
                  if (token != generation || isFinishing()) return;
                  busy = false;
                  updateBulkBar();
                  updateAiBulkBar();
                  try {
                    done.accept(result);
                  } catch (Exception e) {
                    error(e.getMessage());
                  }
                });
          } catch (Exception e) {
            main.post(
                () -> {
                  if (token == generation && !isFinishing()) {
                    busy = false;
                    updateBulkBar();
                    updateAiBulkBar();
                    error(e.getMessage());
                  }
                });
          }
        });
  }

  private void error(String reason) {
    LinearLayout c = card();
    c.addView(text("暂时无法完成", 18, INK, true));
    gap(c, 8);
    c.addView(text(reason == null ? "请重试" : reason, 14, MUTED, false));
    addButton(c, "重新连接", () -> select(tab), true);
    addButton(
        c,
        "打开 KernelSU",
        () -> {
          Intent i = getPackageManager().getLaunchIntentForPackage("me.weishu.kernelsu");
          if (i != null) startActivity(i);
          else toast("请在 Root 管理器中授权信息收件箱");
        },
        false);
  }

  private void toast(String message) {
    Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
  }

  private void progress(String s) {
    LinearLayout c = card();
    c.addView(text(s, 16, MUTED, false));
  }

  private void home() {
    page("信息收件箱", "原始消息 → 待确认建议 → 事项与记忆");
    progress("正在连接手机采集库…");
    async(
        () -> {
          root.ensureSignals();
          return root.status();
        },
        value -> {
          body.removeAllViews();
          JSONObject s = (JSONObject) value;
          LinearLayout state = card();
          boolean installed = s.optBoolean("installed"), running = s.optBoolean("running");
          boolean healthy = running && s.optBoolean("healthy") && !s.optBoolean("error");
          state.addView(
              text(
                  healthy
                      ? "● 正在手机上采集"
                      : running ? "○ 扫描暂未更新，请检查采集状态" : installed ? "○ 采集已暂停" : "○ 等待初始化",
                  18,
                  healthy ? GREEN : INK,
                  true));
          gap(state, 8);
          freshLabel = text("最近采集：" + date(s.optLong("last_success") * 1000), 14, MUTED, false);
          state.addView(freshLabel);
          if (s.optBoolean("error")) {
            gap(state, 8);
            state.addView(text("上一轮读取失败，采集程序会继续重试", 14, 0xFF9A6130, false));
          }
          if (!installed) {
            state.addView(text("首次使用需要在 Root 管理器中允许此 App。初始化后开启手机采集及开机恢复。", 14, MUTED, false));
            addButton(
                state,
                "初始化并启动",
                () ->
                    operation(
                        () -> {
                          root.initialize();
                          root.autostart(true);
                          root.control("start");
                          return null;
                        }),
                true);
          } else {
            addButton(
                state,
                running ? "暂停采集" : "启动采集",
                () -> operation(() -> root.control(running ? "stop" : "start")),
                true);
            addButton(state, "立即采集", () -> operation(() -> root.control("once")), false);
          }
          LinearLayout counts = card();
          counts.addView(text("已保存的信息", 16, INK, true));
          gap(counts, 12);
          LinearLayout row = new LinearLayout(this);
          String[] values = {
            String.valueOf(s.optInt("collected_messages")),
            String.valueOf(s.optInt("conversations")),
            String.valueOf(memories.list().length())
          };
          String[] labels = {"消息", "会话", "确认记忆"};
          for (int i = 0; i < 3; i++) {
            LinearLayout box = column();
            TextView number = text(values[i], 25, PURPLE, true);
            if (i == 0) messageCount = number;
            if (i == 1) conversationCount = number;
            box.addView(number);
            box.addView(text(labels[i], 12, MUTED, false));
            row.addView(box, new LinearLayout.LayoutParams(0, -2, 1));
          }
          counts.addView(row);
          LinearLayout agenda = card();
          agenda.addView(text("今日事项", 18, INK, true));
          gap(agenda, 8);
          agenda.addView(text("查看日程、截止、等待项和已确认的变更。", 14, MUTED, false));
          addButton(
              agenda,
              "查看今日事项",
              () -> startActivity(new Intent(this, OrganizerActivity.class)),
              true);
          LinearLayout flow = card();
          flow.addView(text("先保留出处，再整理记忆", 18, INK, true));
          gap(flow, 8);
          flow.addView(text("微信原消息留在本地采集库。你可以从线索或会话中选取信息，确认标题后保存为记忆，随时回看当时的原文。", 14, MUTED, false));
          addButton(
              flow,
              "查看待整理线索",
              () -> {
                conversationMode = false;
                select(1);
              },
              false);
          LinearLayout boot = card();
          Switch toggle = new Switch(this);
          toggle.setText("开机恢复采集");
          toggle.setTextSize(16);
          toggle.setTextColor(INK);
          toggle.setChecked(s.optBoolean("autostart"));
          toggle.setEnabled(installed);
          toggle.setOnCheckedChangeListener(
              (view, on) ->
                  operation(
                      () -> {
                        root.autostart(on);
                        return null;
                      }));
          boot.addView(toggle);
          gap(boot, 8);
          boot.addView(text("开启后由 Root 启动脚本恢复采集，手机解锁后可继续读取微信记录。", 13, MUTED, false));
          LinearLayout ai = card();
          boolean ready = AnalysisRouting.ready(this);
          ai.addView(text(ready ? "AI 整理已就绪" : "连接宁宁进行整理", 16, INK, true));
          gap(ai, 6);
          ai.addView(
              text(
                  AnalysisRouting.standalone(this)
                      ? "独立接口模式；结果需要核对。"
                      : "模型与密钥由宁宁后端管理，手机负责采集、确认和通知。",
                  14,
                  MUTED,
                  false));
          addButton(
              ai,
              ready ? "整理一批" : "连接宁宁",
              () -> {
                if (ready) analyze();
                else select(4);
              },
              true);
          addButton(ai, "AI建议", this::aiInbox, false);
          dashboardRunning = running;
          dashboardReady = true;
        });
  }

  private void refreshHomeStats() {
    async(
        () -> root.status(),
        value -> {
          JSONObject stats = (JSONObject) value;
          if (stats.optBoolean("running") != dashboardRunning) {
            home();
            return;
          }
          freshLabel.setText("最近采集：" + date(stats.optLong("last_success") * 1000));
          messageCount.setText(String.valueOf(stats.optInt("collected_messages")));
          conversationCount.setText(String.valueOf(stats.optInt("conversations")));
        });
  }

  private void operation(Task task) {
    page("正在处理", "操作完成后会刷新采集状态");
    progress("请稍候…");
    async(
        task,
        result -> {
          toast("操作完成");
          select(0);
        });
  }

  private void inbox() {
    page(
        excludedInboxMode ? "已排除的会话" : "信息收件箱",
        excludedInboxMode
            ? "已停止 AI 整理，原文仍保存在本机"
            : conversationMode ? "已排除会话默认收起" : "最近 7 天的本地线索，需要你核对");
    LinearLayout chooser = card();
    LinearLayout row = new LinearLayout(this);
    row.setGravity(Gravity.CENTER_VERTICAL);

    row.addView(
        button(
            "待整理线索",
            () -> {
              conversationMode = false;
              excludedInboxMode = false;
              inbox();
            },
            !conversationMode && !excludedInboxMode),
        new LinearLayout.LayoutParams(0, -2, 1));
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, -2, 1);
    p.setMargins(dp(8), 0, 0, 0);
    row.addView(
        button(
            "会话列表",
            () -> {
              conversationMode = true;
              excludedInboxMode = false;
              inbox();
            },
            conversationMode && !excludedInboxMode),
        p);
    chooser.addView(row);
    gap(chooser, 12);
    int excludedCount = new ConversationPolicy(memories).count();
    if (excludedInboxMode || excludedCount > 0) {
      addButton(
          chooser,
          excludedInboxMode ? "返回常规收件箱" : "查看已排除的会话（" + excludedCount + "）",
          () -> {
            excludedInboxMode = !excludedInboxMode;
            conversationMode = true;
            inbox();
          },
          false);
      gap(chooser, 12);
    }
    if (conversationMode) {
      EditText search = new EditText(this);
      search.setSingleLine(true);
      search.setHint("搜索联系人或会话");
      search.setText(conversationSearch);
      chooser.addView(search);
      addButton(chooser, "搜索", () -> loadConversations(search.getText().toString()), false);
      addButton(chooser, "多选", this::toggleMultiSelect, false);
      loadConversations(conversationSearch);
    } else {
      chooser.addView(text("关键词线索不是 AI 判断。已处理、忽略及已排除会话的线索默认收起；原文保留。最多展示 100 条。", 13, MUTED, false));
      async(
          () -> root.candidates(memories),
          result -> {
            JSONArray list = (JSONArray) result;
            int shown = 0;
            for (int i = 0; i < list.length(); i++) {
              JSONObject message = list.getJSONObject(i);
              if (!memories.state(message).equals("pending")) continue;
              sourceCard(message, true);
              shown++;
            }
            if (shown == 0) {
              LinearLayout c = card();
              c.addView(text("暂无线索待确认", 17, INK, true));
              gap(c, 8);
              c.addView(text("可以到会话列表中挑选值得保存的信息；已排除会话单独查看。", 14, MUTED, false));
            }
          });
    }
  }

  private void loadConversations(String search) {
    conversationSearch = search;
    prepareRoomLoad();
    while (body.getChildCount() > 1) body.removeViewAt(1);
    async(
        () -> root.conversations(memories, search, excludedInboxMode),
        result -> {
          JSONArray list = (JSONArray) result;
          setRoomPage(
              list,
              () -> {
                try {
                  renderConversationRows(list);
                } catch (Exception error) {
                  error("读取会话失败");
                }
              },
              this::inbox,
              !excludedInboxMode,
              excludedInboxMode);
          renderConversationRows(list);
        });
  }

  private void renderConversationRows(JSONArray list) throws Exception {
    while (body.getChildCount() > 1) body.removeViewAt(1);
    selectionBoxes.clear();
    if (list.length() == 0) {
      LinearLayout c = card();
      c.addView(text(excludedInboxMode ? "没有匹配的已排除会话" : "没有匹配的会话", 16, MUTED, false));
    }
    for (int i = 0; i < list.length(); i++) {
      JSONObject conversation = list.getJSONObject(i);
      LinearLayout c = card();
      roomHeading(c, conversation);
      gap(c, 6);
      c.addView(
          text(
              conversation.getInt("message_count")
                  + " 条消息 · "
                  + date(conversation.getLong("latest_at")),
              13,
              MUTED,
              false));
      c.setOnClickListener(v -> roomClick(conversation, () -> messages(conversation, "", 0)));
      if (new ConversationPolicy(memories).excluded(conversation)) {
        gap(c, 6);
        c.addView(text("已排除 AI 整理 · 原文保留", 13, PURPLE, false));
      }
      c.setOnLongClickListener(v -> roomLongClick(conversation));
      c.setContentDescription(
          (multiSelectMode ? "选择会话 " : "打开会话 ")
              + MessagePresentation.conversationTitle(conversation));
    }
    if (list.length() == 100) {
      LinearLayout c = card();
      c.addView(text("显示最近 100 个会话，全选只选择当前页。可搜索其他会话。", 13, MUTED, false));
    }
  }

  private void messages(JSONObject conversation, String searchValue, int offset) {
    page(MessagePresentation.conversationTitle(conversation), "原始消息 · 点开消息可保存记忆");
    LinearLayout toolbar = card();
    addButton(
        toolbar,
        "返回会话列表",
        () -> {
          conversationMode = true;
          inbox();
        },
        false);
    EditText search = new EditText(this);
    search.setSingleLine(true);
    search.setHint("搜索此会话消息");
    search.setText(searchValue);
    toolbar.addView(search);
    addButton(toolbar, "搜索消息", () -> messages(conversation, search.getText().toString(), 0), false);
    addButton(
        toolbar,
        "此会话的 AI 整理设置",
        () ->
            conversationPolicyDialog(
                conversation, () -> messages(conversation, searchValue, offset)),
        false);
    if (new ConversationPolicy(memories).excluded(conversation)) {
      gap(toolbar, 8);
      toolbar.addView(text("已排除 AI 整理，仍采集并保留本机原文", 13, PURPLE, true));
    }
    async(
        () ->
            root.messages(
                conversation.getString("account_id"),
                conversation.getString("talker"),
                searchValue,
                offset),
        result -> {
          JSONArray list = (JSONArray) result;
          for (int i = 0; i < list.length(); i++) sourceCard(list.getJSONObject(i), false);
          LinearLayout footer = card();
          if (list.length() == 0) footer.addView(text("没有更多匹配消息", 14, MUTED, false));
          if (offset > 0)
            addButton(
                footer,
                "上一页",
                () -> messages(conversation, searchValue, Math.max(0, offset - 50)),
                false);
          if (list.length() == 50)
            addButton(
                footer, "更早的消息", () -> messages(conversation, searchValue, offset + 50), false);
        });
  }

  private void sourceCard(JSONObject message, boolean candidate) {
    LinearLayout c = card();
    String content = display(message);
    c.addView(
        text(
            candidate
                ? SourceRules.label(content)
                : message.optInt("is_send") == 1 ? "我发出的消息" : "收到的消息",
            13,
            PURPLE,
            true));
    gap(c, 6);
    String sender = MessagePresentation.senderName(message);
    if (!sender.isEmpty()) {
      c.addView(text(sender, 13, MUTED, true));
      gap(c, 6);
    }
    TextView preview = text(content, 15, INK, false);
    preview.setMaxLines(candidate ? 4 : 7);
    preview.setEllipsize(TextUtils.TruncateAt.END);
    c.addView(preview);
    gap(c, 9);
    c.addView(
        text(
            MessagePresentation.conversationTitle(message)
                + " · "
                + date(message.optLong("created_at")),
            12,
            MUTED,
            false));
    gap(c, 6);
    c.addView(text(sourceProgressLabel(message), 12, PURPLE, false));
    c.setOnClickListener(v -> sourceDialog(message, candidate));
  }

  private void sourceDialog(JSONObject source, boolean candidate) {
    LinearLayout view = column();
    view.setPadding(dp(20), dp(8), dp(20), 0);
    view.addView(
        text(
            MessagePresentation.conversationTitle(source)
                + " · "
                + date(source.optLong("created_at")),
            13,
            MUTED,
            false));
    gap(view, 6);
    view.addView(text(sourceProgressLabel(source), 13, PURPLE, false));
    gap(view, 10);
    String sender = MessagePresentation.senderName(source);
    if (!sender.isEmpty()) {
      view.addView(text(sender, 14, INK, true));
      gap(view, 8);
    }
    TextView quote = text(display(source), 15, INK, false);
    quote.setTextIsSelectable(true);
    view.addView(quote);
    if (source.optInt("quote_truncated") == 1) {
      gap(view, 8);
      view.addView(text("此消息较长，展示及引用前 20,000 字。", 12, MUTED, false));
    }
    ScrollView scroll = new ScrollView(this);
    scroll.addView(view);
    AlertDialog.Builder dialog =
        new AlertDialog.Builder(this)
            .setTitle("消息出处")
            .setView(scroll)
            .setNegativeButton("关闭", null);
    AlertDialog shown = dialog.create();
    addButton(
        view,
        "查看这条消息的 AI 整理结果",
        () -> {
          shown.dismiss();
          aiSection = "all";
          aiInbox(source);
        },
        true);
    addButton(
        view,
        "保存为记忆",
        () -> {
          shown.dismiss();
          saveDialog(source);
        },
        false);
    addButton(
        view,
        "更多整理操作",
        () -> {
          shown.dismiss();
          new AlertDialog.Builder(this)
              .setTitle("整理这条消息")
              .setItems(
                  new String[] {"建立待办", "建立日程", "标为已处理", "忽略"},
                  (a, index) -> {
                    try {
                      if (index < 2)
                        openProposal(
                            new OrganizerStore(memories)
                                .manual(
                                    source,
                                    index == 0 ? "todo" : "event",
                                    MessagePresentation.body(source)
                                        .substring(
                                            0,
                                            Math.min(
                                                80, MessagePresentation.body(source).length()))));
                      else {
                        memories.mark(source, index == 2 ? "handled" : "ignored");
                        inbox();
                      }
                    } catch (Exception e) {
                      toast("保存状态失败");
                    }
                  })
              .show();
        },
        false);
    shown.show();
  }

  private void saveDialog(JSONObject source) {
    saveDialog(source, MessagePresentation.body(source), "记忆");
  }

  private void saveDialog(JSONObject source, String presetTitle, String presetKind) {
    saveDialog(source, presetTitle, presetKind, null);
  }

  private void saveDialog(
      JSONObject source, String presetTitle, String presetKind, JSONObject proposal) {
    LinearLayout view = column();
    view.setPadding(dp(20), dp(8), dp(20), 0);
    view.addView(text("填写你确认后的内容，原消息将作为引用保留。", 14, MUTED, false));
    EditText title = new EditText(this);
    title.setHint("记忆标题");
    title.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
    title.setText(presetTitle.length() > 200 ? presetTitle.substring(0, 200) : presetTitle);
    view.addView(title);
    String[] kinds = {"记忆", "决定", "资料"};
    Spinner kind = new Spinner(this);
    kind.setAdapter(
        new ArrayAdapter<String>(this, android.R.layout.simple_spinner_dropdown_item, kinds));
    for (int i = 0; i < kinds.length; i++) if (kinds[i].equals(presetKind)) kind.setSelection(i);
    view.addView(kind);
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle("确认记忆")
            .setView(view)
            .setPositiveButton("确认保存", null)
            .setNegativeButton("取消", null)
            .create();
    dialog.setOnShowListener(
        d ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    v -> {
                      try {
                        if (proposal == null)
                          memories.save(
                              source,
                              title.getText().toString(),
                              kind.getSelectedItem().toString());
                        else
                          new OrganizerStore(memories)
                              .confirmMemory(
                                  proposal,
                                  title.getText().toString(),
                                  kind.getSelectedItem().toString());
                        dialog.dismiss();
                        toast("已保存，并保留消息出处");
                        select(3);
                      } catch (Exception e) {
                        title.setError(e.getMessage());
                      }
                    }));
    dialog.show();
  }

  private void memoryPage() {
    page("确认后的记忆", "由你确认，保留当时的原消息引用");
    try {
      JSONArray list = memories.list();
      LinearLayout overview = card();
      overview.addView(text(list.length() + " 条确认记录", 18, INK, true));
      addButton(overview, "导出确认记忆", () -> shareMemories(), false);
      if (list.length() == 0) {
        LinearLayout c = card();
        c.addView(text("从一条重要消息开始", 18, INK, true));
        gap(c, 8);
        c.addView(text("在收件箱中点开消息，填写标题并确认保存。后续 ChatWaifu 可以依据这些确认记录和出处整理长期记忆。", 14, MUTED, false));
      }
      for (int i = 0; i < list.length(); i++) {
        JSONObject item = list.getJSONObject(i), source = item.getJSONObject("source");
        LinearLayout c = card();
        c.addView(text(item.getString("kind") + " · 已确认", 12, PURPLE, true));
        gap(c, 6);
        c.addView(text(item.getString("title"), 17, INK, true));
        gap(c, 8);
        c.addView(
            text(
                MessagePresentation.conversationTitle(source)
                    + " · "
                    + date(source.optLong("created_at")),
                12,
                MUTED,
                false));
        c.setOnClickListener(v -> memoryDialog(item));
      }
    } catch (Exception e) {
      error("读取确认记忆失败");
    }
  }

  private void memoryDialog(JSONObject item) {
    try {
      JSONObject source = item.getJSONObject("source");
      LinearLayout view = column();
      view.setPadding(dp(20), dp(8), dp(20), 0);
      view.addView(text("确认内容", 13, PURPLE, true));
      gap(view, 6);
      view.addView(text(item.getString("title"), 17, INK, true));
      gap(view, 16);
      view.addView(
          text(
              "原消息快照 · "
                  + MessagePresentation.conversationTitle(source)
                  + " · "
                  + date(source.optLong("created_at")),
              12,
              MUTED,
              false));
      gap(view, 8);
      String sender = MessagePresentation.senderName(source);
      if (!sender.isEmpty()) view.addView(text(sender, 13, PURPLE, true));
      TextView quote =
          text(
              MessagePresentation.body(
                  new JSONObject(source.toString()).put("content", item.getString("quote"))),
              15,
              INK,
              false);
      quote.setTextIsSelectable(true);
      view.addView(quote);
      ScrollView scroll = new ScrollView(this);
      scroll.addView(view);
      new AlertDialog.Builder(this)
          .setTitle("记忆与出处")
          .setView(scroll)
          .setPositiveButton(
              "编辑确认内容",
              (d, w) -> saveDialog(source, item.optString("title"), item.optString("kind")))
          .setNegativeButton("关闭", null)
          .setNeutralButton(
              "删除",
              (d, w) ->
                  new AlertDialog.Builder(this)
                      .setTitle("删除这条确认记忆？")
                      .setMessage("原始采集消息仍可在会话中查看。")
                      .setPositiveButton(
                          "删除",
                          (x, y) -> {
                            memories.remove(item.optString("ref"));
                            memoryPage();
                          })
                      .setNegativeButton("取消", null)
                      .show())
          .show();
    } catch (Exception e) {
      toast("读取出处失败");
    }
  }

  private void shareMemories() {
    try {
      File dir = new File(getFilesDir(), "exports");
      if (!dir.exists() && !dir.mkdirs()) throw new Exception();
      File file = new File(dir, "memories.json");
      try (FileOutputStream out = new FileOutputStream(file)) {
        out.write(memories.exportDocument().toString(2).getBytes(StandardCharsets.UTF_8));
      }
      Intent i =
          new Intent(Intent.ACTION_SEND)
              .setType("application/json")
              .putExtra(Intent.EXTRA_STREAM, ExportProvider.URI)
              .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
      startActivity(Intent.createChooser(i, "导出确认记忆"));
    } catch (Exception e) {
      toast("导出失败，请重试");
    }
  }

  private void resetSelectionPage() {
    multiSelectMode = false;
    selection.all(false);
    selectionBoxes.clear();
    redrawRooms = refreshRooms = null;
    if (bulkBar != null) outer.removeView(bulkBar);
    bulkBar = null;
    bulkCount = null;
    bulkAll = bulkExclude = bulkRestore = null;
    try {
      selection.replaceVisible(new JSONArray());
    } catch (Exception impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private void prepareRoomLoad() {
    try {
      selection.replaceVisible(new JSONArray());
    } catch (Exception impossible) {
      throw new IllegalStateException(impossible);
    }
    selectionBoxes.clear();
    redrawRooms = refreshRooms = null;
    updateBulkBar();
  }

  private void setRoomPage(
      JSONArray rooms, Runnable redraw, Runnable refresh, boolean canExclude, boolean canRestore)
      throws Exception {
    selection.replaceVisible(rooms);
    redrawRooms = redraw;
    refreshRooms = refresh;
    bulkExcludeAllowed = canExclude;
    bulkRestoreAllowed = canRestore;
    if (multiSelectMode) createBulkBar();
  }

  private void toggleMultiSelect() {
    if (!multiSelectMode && (redrawRooms == null || selection.visibleCount() == 0)) {
      toast("请先等待会话加载完成");
      return;
    }
    multiSelectMode = !multiSelectMode;
    selection.all(false);
    if (multiSelectMode) createBulkBar();
    else {
      if (bulkBar != null) outer.removeView(bulkBar);
      bulkBar = null;
    }
    redrawSelectionRows();
  }

  private void redrawSelectionRows() {
    if (redrawRooms == null) return;
    ScrollView scroll = (ScrollView) body.getParent();
    int y = scroll.getScrollY();
    selectionBoxes.clear();
    redrawRooms.run();
    scroll.post(() -> scroll.scrollTo(0, y));
    updateBulkBar();
  }

  private void createBulkBar() {
    if (bulkBar != null) outer.removeView(bulkBar);
    bulkBar = column();
    bulkBar.setPadding(dp(18), dp(8), dp(18), dp(8));
    bulkBar.setBackgroundColor(Color.WHITE);
    LinearLayout top = new LinearLayout(this);
    top.setGravity(Gravity.CENTER_VERTICAL);
    bulkCount = text("", 13, MUTED, false);
    top.addView(bulkCount, new LinearLayout.LayoutParams(0, -2, 1));
    bulkAll =
        button(
            "全选本页",
            () -> {
              selection.all(selection.count() != selection.visibleCount());
              for (CheckBox box : selectionBoxes.values()) {
                JSONObject room = (JSONObject) box.getTag();
                box.setChecked(selection.selected(room));
              }
              updateBulkBar();
            },
            false);
    top.addView(bulkAll, new LinearLayout.LayoutParams(-2, -2));
    bulkBar.addView(top);
    gap(bulkBar, 6);
    LinearLayout actions = new LinearLayout(this);
    actions.setGravity(Gravity.CENTER_VERTICAL);
    addBulkAction(actions, button("取消多选", this::toggleMultiSelect, false));
    bulkExclude = bulkRestore = null;
    if (bulkExcludeAllowed) {
      bulkExclude = button("批量排除", () -> confirmBulkPolicy(true), true);
      addBulkAction(actions, bulkExclude);
    }
    if (bulkRestoreAllowed) {
      bulkRestore = button("批量恢复", () -> confirmBulkPolicy(false), true);
      addBulkAction(actions, bulkRestore);
    }
    bulkBar.addView(actions);
    outer.addView(bulkBar, outer.indexOfChild(nav), new LinearLayout.LayoutParams(-1, -2));
    updateBulkBar();
  }

  private void addBulkAction(LinearLayout row, Button action) {
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
    if (row.getChildCount() > 0) params.setMargins(dp(8), 0, 0, 0);
    row.addView(action, params);
  }

  private void updateBulkBar() {
    if (!multiSelectMode || bulkBar == null) return;
    bulkCount.setText(
        (busy ? "正在处理，请稍候\n已选 " : "已选 ")
            + selection.count()
            + " / 本页 "
            + selection.visibleCount()
            + (busy ? " 个" : " 个\n全选仅限当前显示的会话"));
    for (CheckBox box : selectionBoxes.values()) box.setEnabled(!busy);
    bulkAll.setText(
        selection.visibleCount() > 0 && selection.count() == selection.visibleCount()
            ? "取消全选"
            : "全选本页");
    bulkAll.setEnabled(!busy && selection.visibleCount() > 0);
    if (bulkExclude != null) {
      bulkExclude.setEnabled(!busy && selection.count() > 0);
      bulkExclude.setAlpha(bulkExclude.isEnabled() ? 1 : 0.45f);
    }
    if (bulkRestore != null) {
      bulkRestore.setEnabled(!busy && selection.count() > 0);
      bulkRestore.setAlpha(bulkRestore.isEnabled() ? 1 : 0.45f);
    }
  }

  private void roomHeading(LinearLayout card, JSONObject room) {
    String title = MessagePresentation.conversationTitle(room);
    if (!multiSelectMode) {
      card.addView(text(title, 17, INK, true));
      return;
    }
    LinearLayout row = new LinearLayout(this);
    row.setGravity(Gravity.CENTER_VERTICAL);
    CheckBox check = new CheckBox(this);
    check.setMinHeight(dp(48));
    check.setMinWidth(dp(48));
    check.setContentDescription("选择会话 " + title);
    check.setTag(room);
    check.setChecked(selection.selected(room));
    check.setOnCheckedChangeListener(
        (view, value) -> {
          selection.select(room, value);
          updateBulkBar();
        });
    selectionBoxes.put(ConversationSelection.key(room), check);
    row.addView(check, new LinearLayout.LayoutParams(dp(48), -2));
    row.addView(text(title, 17, INK, true), new LinearLayout.LayoutParams(0, -2, 1));
    card.addView(row);
  }

  private void roomClick(JSONObject room, Runnable normal) {
    if (busy) return;
    if (multiSelectMode) {
      CheckBox box = selectionBoxes.get(ConversationSelection.key(room));
      if (box != null) box.setChecked(!box.isChecked());
    } else normal.run();
  }

  private boolean roomLongClick(JSONObject room) {
    if (busy) return true;
    if (!multiSelectMode) toggleMultiSelect();
    CheckBox box = selectionBoxes.get(ConversationSelection.key(room));
    if (box != null) box.setChecked(true);
    return true;
  }

  private void confirmBulkPolicy(boolean exclude) {
    try {
      if (busy || selection.count() == 0) return;
      JSONArray selected = selection.snapshot();
      Runnable refresh = refreshRooms;
      LinearLayout content = column();
      content.setPadding(dp(24), dp(12), dp(24), dp(8));
      content.addView(
          text(
              exclude
                  ? "选中的会话将停止 AI 整理，并从常规收件箱收起。本机原文、已有事项和提醒保留。已发出的请求无法撤回，其返回结果会被拦截。"
                  : "选中的会话将恢复 AI 整理，重新显示在常规收件箱。自动整理开启时，会继续处理范围内保留的资料。",
              14,
              MUTED,
              false));
      gap(content, 12);
      StringBuilder names = new StringBuilder();
      for (int i = 0; i < Math.min(5, selected.length()); i++) {
        if (i > 0) names.append("、");
        names.append(MessagePresentation.conversationTitle(selected.getJSONObject(i)));
      }
      if (selected.length() > 5) names.append("等");
      content.addView(text(names.toString(), 13, MUTED, false));
      ScrollView confirmScroll = new ScrollView(this);
      confirmScroll.addView(content);
      AlertDialog dialog =
          new AlertDialog.Builder(this)
              .setTitle((exclude ? "排除 " : "恢复 ") + selected.length() + " 个会话？")
              .setView(confirmScroll)
              .setNegativeButton("返回选择", null)
              .create();
      addButton(
          content,
          exclude ? "确认批量排除" : "确认批量恢复",
          () -> {
            dialog.dismiss();
            async(
                () -> {
                  int changed = new ConversationPolicy(memories).setMany(selected, exclude);
                  AnalysisJob.enqueueIntake(this, 1000L);
                  return changed;
                },
                result -> {
                  toast("已" + (exclude ? "排除 " : "恢复 ") + result + " 个会话");
                  if (multiSelectMode) toggleMultiSelect();
                  refresh.run();
                });
            updateBulkBar();
          },
          true);
      dialog.show();
    } catch (Exception error) {
      toast("读取选择失败，请重新选择");
    }
  }

  private void conversationPolicyDialog(JSONObject room, Runnable refresh) {
    boolean excluded = new ConversationPolicy(memories).excluded(room);
    LinearLayout content = column();
    content.setPadding(dp(24), dp(12), dp(24), dp(8));
    content.addView(
        text(
            excluded
                ? "恢复后，会继续整理范围内保留的资料。已有的记忆、事项和提醒会保留。"
                : "原文继续采集并保存在本机。排队中的消息暂停整理，已有记忆、事项和提醒保留。已发出的请求无法撤回，其返回结果不会生成新建议。",
            14,
            MUTED,
            false));
    gap(content, 12);
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(MessagePresentation.conversationTitle(room))
            .setView(content)
            .setNegativeButton("关闭", null)
            .create();
    addButton(
        content,
        excluded ? "恢复 AI 整理" : "排除 AI 整理",
        () -> {
          dialog.dismiss();
          async(
              () -> {
                new ConversationPolicy(memories).set(room, !excluded);
                AnalysisJob.enqueueIntake(this, 1000L);
                return null;
              },
              result -> {
                toast(excluded ? "已恢复 AI 整理" : "已排除 AI 整理，原文保留");
                refresh.run();
              });
        },
        true);
    dialog.show();
  }

  private void exclusionSettings() {
    page("AI 整理排除列表", "按会话排除，保留本机原文");
    LinearLayout controls = card();
    addButton(controls, "返回设置", this::settings, false);
    EditText search = new EditText(this);
    search.setSingleLine(true);
    search.setHint("搜索群名称、好友昵称或备注");
    controls.addView(search);
    addButton(controls, "查找群和好友", () -> loadPolicyRooms(search.getText().toString(), false), true);
    addButton(controls, "查看已排除", () -> loadPolicyRooms(search.getText().toString(), true), false);
    addButton(controls, "多选", this::toggleMultiSelect, false);
    gap(controls, 12);
    controls.addView(text("点击“多选”或长按会话，可批量排除或恢复。", 13, MUTED, false));
    loadPolicyRooms("", true);
  }

  private void loadPolicyRooms(String search, boolean excludedOnly) {
    prepareRoomLoad();
    while (body.getChildCount() > 1) body.removeViewAt(1);
    async(
        () ->
            excludedOnly
                ? new ConversationPolicy(memories).list()
                : root.policyConversations(search),
        result -> {
          JSONArray input = (JSONArray) result, list = new JSONArray();
          int matched = 0;
          for (int i = 0; i < input.length(); i++) {
            JSONObject room = input.getJSONObject(i);
            String title = MessagePresentation.conversationTitle(room);
            if (excludedOnly
                && !title
                    .toLowerCase(java.util.Locale.ROOT)
                    .contains(search.toLowerCase(java.util.Locale.ROOT))) continue;
            matched++;
            if (list.length() < 100) list.put(room);
          }
          boolean limited = matched > 100 || (!excludedOnly && input.length() == 100);
          setRoomPage(
              list,
              () -> {
                try {
                  renderPolicyRows(list, search, excludedOnly, limited);
                } catch (Exception error) {
                  error("读取会话失败");
                }
              },
              () -> loadPolicyRooms(search, excludedOnly),
              !excludedOnly,
              true);
          renderPolicyRows(list, search, excludedOnly, limited);
        });
  }

  private void renderPolicyRows(
      JSONArray list, String search, boolean excludedOnly, boolean limited) throws Exception {
    while (body.getChildCount() > 1) body.removeViewAt(1);
    selectionBoxes.clear();
    ConversationPolicy policy = new ConversationPolicy(memories);
    for (int i = 0; i < list.length(); i++) {
      JSONObject room = list.getJSONObject(i);
      boolean excluded = policy.excluded(room);
      LinearLayout c = card();
      roomHeading(c, room);
      gap(c, 6);
      c.addView(
          text(
              (room.getString("talker").endsWith("@chatroom") ? "群聊" : "联系人")
                  + " · "
                  + (excluded ? "已排除 AI 整理" : "参与 AI 整理"),
              13,
              MUTED,
              false));
      if (!multiSelectMode)
        addButton(
            c,
            excluded ? "恢复此会话整理" : "排除此会话整理",
            () -> conversationPolicyDialog(room, () -> loadPolicyRooms(search, excludedOnly)),
            false);
      c.setOnClickListener(
          v ->
              roomClick(
                  room,
                  () ->
                      conversationPolicyDialog(room, () -> loadPolicyRooms(search, excludedOnly))));
      c.setOnLongClickListener(v -> roomLongClick(room));
    }
    if (list.length() == 0) {
      LinearLayout c = card();
      c.addView(text(excludedOnly ? "没有匹配的已排除会话" : "没有匹配的会话", 16, MUTED, false));
      gap(c, 8);
      c.addView(text("点击“查找群和好友”选择要排除的会话。", 14, MUTED, false));
    }
    if (limited) {
      LinearLayout c = card();
      c.addView(text("显示前 100 个匹配会话，全选只选择当前页。输入名称或备注可缩小范围。", 13, MUTED, false));
    }
  }

  private void settings() {
    page("设置与连接", "手机负责采集与通知，宁宁后端负责 AI 整理");
    LinearLayout exclusions = card();
    exclusions.addView(text("AI 整理范围", 18, INK, true));
    gap(exclusions, 8);
    exclusions.addView(text("排除群或好友后，原文继续保存在本机，停止提交 AI 整理。", 14, MUTED, false));
    addButton(exclusions, "管理排除的群和好友", this::exclusionSettings, false);
    ApiSettings api = new ApiSettings(this);
    BackendSettings backend = new BackendSettings(this);
    LinearLayout connection = card();
    connection.addView(text("连接宁宁", 18, INK, true));
    gap(connection, 8);
    connection.addView(text("使用宁宁后端现有的模型配置。手机只需连接服务器，无需填写模型名称或模型 API Key。", 14, MUTED, false));
    EditText server = new EditText(this);
    server.setSingleLine(true);
    server.setHint("宁宁服务器根地址");
    server.setText(backend.base());
    connection.addView(server);
    EditText credential = new EditText(this);
    credential.setSingleLine(true);
    credential.setHint(backend.hasCredential() ? "连接凭据已保存；留空保留" : "宁宁后端连接凭据（与桌面连接相同）");
    credential.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
    connection.addView(credential);
    addButton(
        connection,
        "连接并验证宁宁",
        () -> {
          try {
            backend.save(server.getText().toString(), credential.getText().toString());
            credential.setText("");
          } catch (Exception error) {
            toast(error.getMessage());
            return;
          }
          async(
              () -> backend.client().connect(),
              result -> {
                toast((String) result);
                AnalysisRouting.standalone(this, false);
                settings();
              });
        },
        true);
    connection.addView(
        text(backend.ready() ? "已连接宁宁，整理模型由后端管理" : "未完成连接；采集和本地事项继续可用", 13, MUTED, false));

    LinearLayout knowledge = card();
    knowledge.addView(text("个人知识地图", 18, INK, true));
    knowledge.addView(
        text("同步未排除的文字资料与已确认内容，可在网页查看关系、核对出处和编辑事项。图谱需要联网，本地采集和提醒照常工作。", 14, MUTED, false));
    Switch syncKnowledge = new Switch(this);
    syncKnowledge.setText("启用知识库同步");
    syncKnowledge.setChecked(KnowledgeSync.enabled(this));
    syncKnowledge.setOnCheckedChangeListener(
        (view, enabled) -> {
          getSharedPreferences("knowledge", MODE_PRIVATE)
              .edit()
              .putBoolean("enabled", enabled)
              .commit();
          KnowledgeJob.enqueue(this);
        });
    knowledge.addView(syncKnowledge);
    Switch historyKnowledge = new Switch(this);
    historyKnowledge.setText("Wi-Fi 下补齐 30 天以前的资料");
    historyKnowledge.setChecked(
        getSharedPreferences("knowledge", MODE_PRIVATE).getBoolean("history", true));
    historyKnowledge.setOnCheckedChangeListener(
        (view, enabled) ->
            getSharedPreferences("knowledge", MODE_PRIVATE)
                .edit()
                .putBoolean("history", enabled)
                .apply());
    knowledge.addView(historyKnowledge);
    addButton(
        knowledge, "打开知识地图", () -> startActivity(new Intent(this, KnowledgeActivity.class)), true);
    addButton(
        knowledge,
        "立即同步",
        () ->
            async(
                () -> {
                  KnowledgeSync.run(this);
                  return "本轮同步完成，后台会继续补齐资料";
                },
                result -> toast((String) result)),
        false);
    knowledge.addView(
        text(
            getSharedPreferences("knowledge", MODE_PRIVATE)
                .getString("error", "排除规则与版本冲突会随资料一起同步。"),
            13,
            MUTED,
            false));

    LinearLayout process = card();
    process.addView(text("整理方式", 18, INK, true));
    gap(process, 8);
    process.addView(text(AnalysisRouting.status(this), 14, PURPLE, true));
    Switch auto = new Switch(this);
    auto.setText("新消息自动整理");
    auto.setChecked(api.automatic());
    auto.setOnCheckedChangeListener(
        (view, on) -> {
          if (on && !AnalysisRouting.ready(this)) {
            toast(AnalysisRouting.standalone(this) ? "请先保存独立接口配置" : "请先连接宁宁后端");
            view.setChecked(false);
            return;
          }
          api.automatic(on);
          AnalysisJob.schedule(this, on);
          if (on) AnalysisJob.enqueueIntake(this, 1000L);
        });
    process.addView(auto);
    gap(process, 8);
    process.addView(text("新采集批次触发整理，周期任务补偿。资料先保存在手机，结果需确认。后端离线时保留队列和进度。", 13, MUTED, false));
    addButton(process, "立即整理一批", this::analyze, false);
    historyControls(process);
    EditText timezone = new EditText(this);
    timezone.setSingleLine(true);
    timezone.setText(api.timezone());
    process.addView(text("整理与日程时区", 13, MUTED, false));
    process.addView(timezone);
    addButton(
        process,
        "保存时区",
        () -> {
          try {
            api.timezone(timezone.getText().toString().trim());
            toast("时区已保存");
          } catch (Exception e) {
            timezone.setError("请填写有效时区，例如 Asia/Shanghai");
          }
        },
        false);

    LinearLayout advanced = card();
    advanced.addView(text("高级选项", 18, INK, true));
    Switch standalone = new Switch(this);
    standalone.setText("使用独立模型接口");
    standalone.setChecked(AnalysisRouting.standalone(this));
    standalone.setOnCheckedChangeListener(
        (view, on) -> {
          AnalysisRouting.standalone(this, on);
          settings();
        });
    advanced.addView(standalone);
    advanced.addView(text("仅在希望手机自行调用模型时启用。连接宁宁时不需要此项。", 13, MUTED, false));
    if (AnalysisRouting.standalone(this)) {
      EditText base = new EditText(this);
      base.setSingleLine(true);
      base.setHint("独立接口 Base URL");
      base.setText(api.base());
      advanced.addView(base);
      EditText model = new EditText(this);
      model.setSingleLine(true);
      model.setHint("模型 ID");
      model.setText(api.model());
      advanced.addView(model);
      EditText key = new EditText(this);
      key.setSingleLine(true);
      key.setHint(api.hasKey() ? "模型 API Key 已保存，留空保留" : "模型 API Key（无鉴权服务可留空）");
      key.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
      advanced.addView(key);
      Switch json = new Switch(this);
      json.setText("使用 JSON 模式");
      json.setChecked(api.jsonMode());
      advanced.addView(json);
      addButton(
          advanced,
          "保存独立接口配置",
          () -> {
            try {
              api.save(
                  base.getText().toString(),
                  model.getText().toString(),
                  key.getText().toString(),
                  json.isChecked());
              key.setText("");
              toast("独立接口配置已保存");
            } catch (Exception error) {
              toast(error.getMessage());
            }
          },
          false);
      addButton(
          advanced,
          "获取独立接口模型列表",
          () -> {
            try {
              api.save(
                  base.getText().toString(),
                  model.getText().toString(),
                  key.getText().toString(),
                  json.isChecked());
              key.setText("");
            } catch (Exception error) {
              toast(error.getMessage());
              return;
            }
            async(
                () -> api.client().models(),
                result -> {
                  JSONArray models = (JSONArray) result;
                  String[] ids = new String[models.length()];
                  for (int i = 0; i < ids.length; i++)
                    ids[i] = models.getJSONObject(i).getString("id");
                  new AlertDialog.Builder(this)
                      .setTitle("独立接口模型")
                      .setItems(
                          ids,
                          (d, index) -> {
                            model.setText(ids[index]);
                            try {
                              api.save(base.getText().toString(), ids[index], "", json.isChecked());
                            } catch (Exception error) {
                              toast("模型保存失败");
                            }
                          })
                      .setNegativeButton("关闭", null)
                      .show();
                });
          },
          false);
    }
    LinearLayout local = card();
    local.addView(text("本机数据与来源", 18, INK, true));
    gap(local, 8);
    local.addView(text("微信原文、队列、已确认事项和提醒保存在本机。数据库密钥留在 Root 私有目录。", 14, MUTED, false));
    addButton(local, "管理采集与开机恢复", () -> select(0), false);
    addButton(local, "导出已确认记忆", this::shareMemories, false);
    LinearLayout version = card();
    String installedVersion = "";
    try {
      installedVersion = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
    } catch (android.content.pm.PackageManager.NameNotFoundException ignored) {
    }
    version.addView(text("信息收件箱 " + installedVersion + " · 本机试用版", 14, MUTED, false));
    addButton(
        version,
        "打开 Root 管理器",
        () -> {
          Intent i = getPackageManager().getLaunchIntentForPackage("me.weishu.kernelsu");
          if (i != null) startActivity(i);
          else toast("请打开你的 Root 管理器");
        },
        false);
  }

  private void historyControls(LinearLayout parent) {
    gap(parent, 12);
    parent.addView(text("全量历史处理", 16, INK, true));
    parent.addView(
        text("一次启动，持续处理本机已采集、未排除的全部文字记录，生成 AI 建议并建立知识地图。使用 Wi-Fi，原文与进度保留。", 13, MUTED, false));
    importStatus = text(HistoryImport.status(this), 13, PURPLE, false);
    parent.addView(importStatus);
    importAction =
        addButton(
            parent,
            HistoryImport.requested(this) ? "暂停全量处理" : "开始或继续全量处理",
            () -> {
              try {
                if (HistoryImport.requested(this)) HistoryImportService.pause(this);
                else HistoryImportService.start(this);
                if (tab == 4) settings();
                else aiInbox(aiSourceFilter);
              } catch (RuntimeException failure) {
                toast("全量处理尚未开始，请检查后端连接");
              }
            },
            true);
  }

  private void analyze() {
    if (!AnalysisRouting.ready(this)) {
      toast(AnalysisRouting.standalone(this) ? "请先配置独立接口" : "请先连接宁宁后端");
      select(4);
      return;
    }
    new AlertDialog.Builder(this)
        .setTitle("整理一批近期信息？")
        .setMessage(
            AnalysisRouting.standalone(this)
                ? "会把一批资料提交到独立接口。建议需要确认。"
                : "会把一批文字资料与相关上下文提交给宁宁后端，使用后端现有模型整理。手机无需模型密钥，建议需要确认。")
        .setPositiveButton(
            "开始整理",
            (dialog, index) -> {
              page("AI 正在整理", AnalysisRouting.standalone(this) ? "正在请求独立接口…" : "正在提交给宁宁后端…");
              progress("完成后会展示建议及消息出处");
              async(
                  () -> AnalysisEngine.run(this),
                  result -> {
                    toast((String) result);
                    aiInbox();
                  });
            })
        .setNegativeButton("取消", null)
        .show();
  }

  private void aiInbox() {
    aiInbox(null);
  }

  private void aiInbox(JSONObject sourceFilter) {
    aiPageIndex = 0;
    renderAiInbox(sourceFilter, false);
  }

  private void renderAiInbox(JSONObject sourceFilter, boolean retainSelection) {
    JSONArray previous = new JSONArray();
    boolean selecting = retainSelection && aiMultiSelectMode;
    if (selecting)
      try {
        previous = aiSelection.snapshot();
      } catch (Exception ignored) {
      }
    tab = 1;
    renderNav();
    page("AI建议", "先看重要信息与识别时间，再核对来源和保存事项");
    aiMultiSelectMode = selecting;
    aiPageVisible = true;
    aiSourceFilter = sourceFilter;
    LinearLayout sections = card();
    sections.addView(text("建议分区", 16, INK, true));
    gap(sections, 8);
    LinearLayout controls = card();
    controls.addView(text("新消息优先，历史资料逐步补齐", 14, PURPLE, true));
    addButton(controls, "整理下一批", () -> analyze(), true);
    addButton(controls, "刷新整理结果", () -> renderAiInbox(aiSourceFilter, true), false);
    addButton(controls, selecting ? "取消多选" : "多选建议", this::toggleAiMultiSelect, false);
    if (sourceFilter != null) addButton(controls, "查看全部整理建议", this::aiInbox, false);
    addButton(controls, "原文与会话", () -> inbox(), false);
    historyControls(controls);
    gap(controls, 12);
    try {
      OrganizerStore store = new OrganizerStore(memories);
      JSONObject stats = store.queueStats();
      controls.addView(
          text(
              "等待整理 "
                  + stats.optInt("pending")
                  + " 段 · 整理中 "
                  + stats.optInt("leased")
                  + " 段 · 已整理 "
                  + stats.optInt("done")
                  + " 段",
              13,
              MUTED,
              false));
      if (sourceFilter != null) {
        controls.addView(
            text(
                MessagePresentation.conversationTitle(sourceFilter)
                    + " · "
                    + date(sourceFilter.optLong("created_at")),
                13,
                MUTED,
                false));
        controls.addView(text(sourceProgressLabel(sourceFilter), 14, PURPLE, false));
      }
      JSONArray allItems =
          sourceFilter == null ? memories.suggestions() : store.suggestionsForSource(sourceFilter);
      renderAiSections(sections, allItems);
      JSONArray items = AiSuggestionTypes.filter(allItems, aiSection);
      aiPageFingerprint = aiFingerprint(allItems, stats);
      aiPageIndex =
          Math.min(aiPageIndex, Math.max(0, (items.length() - 1) / AiReviewSelection.PAGE_SIZE));
      JSONArray visible = new JSONArray();
      int first = aiPageIndex * AiReviewSelection.PAGE_SIZE;
      for (int i = first; i < Math.min(first + AiReviewSelection.PAGE_SIZE, items.length()); i++)
        visible.put(items.getJSONObject(i));
      aiSelection.replaceVisible(visible);
      for (int i = 0; i < previous.length(); i++) {
        JSONObject old = previous.getJSONObject(i);
        for (int j = 0; j < visible.length(); j++)
          if (visible.getJSONObject(j).toString().equals(old.toString()))
            aiSelection.select(old, true);
      }
      if (items.length() > 0) {
        controls.addView(
            text(
                AiSuggestionTypes.sectionLabel(aiSection)
                    + "分区待确认 "
                    + items.length()
                    + " 条 · 第 "
                    + (aiPageIndex + 1)
                    + " 页（每页最多 100 条）",
                13,
                MUTED,
                false));
        if (first > 0) addButton(controls, "上一页建议", () -> changeAiPage(-1), false);
        if (first + visible.length() < items.length())
          addButton(controls, "下一页建议", () -> changeAiPage(1), false);
        if (selecting)
          controls.addView(text("多选期间暂缓自动刷新，避免打断勾选。全选仅选择本页，换页会清空勾选。", 13, MUTED, false));
      }
      if (items.length() == 0) {
        LinearLayout c = card();
        c.addView(
            text(
                aiSection.equals("all")
                    ? "暂时没有待确认建议"
                    : AiSuggestionTypes.sectionLabel(aiSection) + "分区暂无待确认建议",
                17,
                INK,
                true));
        gap(c, 8);
        c.addView(
            text(
                allItems.length() > 0
                    ? "可切换其他分区查看。新整理结果会自动刷新。"
                    : "结果会自动刷新。等待中的资料会继续分批处理；整理完成也可能没有需要确认的事项。",
                14,
                MUTED,
                false));
      }
      for (int i = 0; i < visible.length(); i++) {
        JSONObject item = visible.getJSONObject(i);
        LinearLayout c = card();
        addAiTypeBadge(c, item);
        gap(c, 6);
        String level = item.getString("importance");
        c.addView(
            text(
                (level.equals("high") ? "高优先级" : level.equals("medium") ? "中优先级" : "低优先级")
                    + " · 待确认",
                12,
                PURPLE,
                true));
        gap(c, 6);
        if (selecting) {
          LinearLayout row = new LinearLayout(this);
          row.setGravity(Gravity.CENTER_VERTICAL);
          CheckBox check = new CheckBox(this);
          check.setMinHeight(dp(48));
          check.setMinWidth(dp(48));
          check.setContentDescription("选择建议 " + item.getString("title"));
          check.setChecked(aiSelection.selected(item));
          check.setOnCheckedChangeListener(
              (view, value) -> {
                aiSelection.select(item, value);
                updateAiBulkBar();
              });
          aiSelectionBoxes.put(AiReviewSelection.key(item), check);
          row.addView(check, new LinearLayout.LayoutParams(dp(48), -2));
          row.addView(
              text(item.getString("title"), 17, INK, true),
              new LinearLayout.LayoutParams(0, -2, 1));
          c.addView(row);
        } else c.addView(text(item.getString("title"), 17, INK, true));
        gap(c, 8);
        String op = item.optString("operation", "create");
        if (!op.equals("create"))
          c.addView(
              text(
                  "已有事项的"
                      + (op.equals("update") ? "变更" : op.equals("complete") ? "完成" : "取消")
                      + "建议",
                  13,
                  PURPLE,
                  false));
        c.addView(text(item.getString("reason"), 14, MUTED, false));
        String times = ProposalTimes.describe(item);
        if (!times.isEmpty()) {
          gap(c, 8);
          c.addView(text(times, 14, PURPLE, true));
        }
        JSONObject evidence = item.getJSONObject("source");
        gap(c, 6);
        c.addView(
            text(
                MessagePresentation.conversationTitle(evidence)
                    + " · "
                    + date(evidence.optLong("created_at")),
                12,
                MUTED,
                false));
        c.setOnClickListener(
            view -> {
              if (busy) return;
              if (aiMultiSelectMode) {
                CheckBox check = aiSelectionBoxes.get(AiReviewSelection.key(item));
                if (check != null) check.setChecked(!check.isChecked());
              } else aiDialog(item);
            });
        c.setOnLongClickListener(
            view -> {
              if (busy) return true;
              if (!aiMultiSelectMode) toggleAiMultiSelect();
              CheckBox check = aiSelectionBoxes.get(AiReviewSelection.key(item));
              if (check != null) check.setChecked(true);
              return true;
            });
        if (selecting) addButton(c, "查看建议出处", () -> aiDialog(item, false), false);
      }
      if (selecting) createAiBulkBar();
    } catch (Exception e) {
      error("读取 AI 建议失败");
    }
  }

  private String sourceProgressLabel(JSONObject source) {
    if (source.optInt("type", 1) != 1) return "非文字消息，暂不进行 AI 整理";
    try {
      JSONObject progress = new OrganizerStore(memories).sourceProgress(source);
      String state = progress.getString("state");
      if (state.equals("excluded")) return "已排除 AI 整理 · 原文保留";
      if (state.equals("outside_scope")) return "未加入近期整理，可在设置加入历史资料";
      if (state.equals("legacy_done")) return "已由旧版整理";
      if (state.equals("not_enqueued")) return "原文已采集，等待加入整理队列";
      if (state.equals("changed")) return "原文已变化，等待重新整理";
      if (state.equals("processing")) return "正在整理";
      if (state.equals("failed")) return "整理未完成，处理进度已保留";
      if (state.equals("done"))
        return progress.optInt("suggestions") > 0
            ? "已整理，生成 " + progress.optInt("suggestions") + " 条建议"
            : "已整理，未提取出需要确认的事项";
      return "等待整理"
          + (progress.optInt("done") > 0
              ? "（已完成 " + progress.optInt("done") + "/" + progress.optInt("total") + " 段）"
              : "");
    } catch (Exception ignored) {
      return "整理状态暂不可用";
    }
  }

  private String aiFingerprint(JSONArray items, JSONObject stats) throws Exception {
    return AnalysisContract.hash(
        items.toString()
            + "\0"
            + stats.toString()
            + "\0"
            + aiSection
            + "\0"
            + (aiSourceFilter == null
                ? ""
                : new OrganizerStore(memories).sourceProgress(aiSourceFilter).toString()));
  }

  private void refreshAiSuggestions() {
    if (aiMultiSelectMode) return;
    try {
      OrganizerStore store = new OrganizerStore(memories);
      JSONArray items =
          aiSourceFilter == null
              ? memories.suggestions()
              : store.suggestionsForSource(aiSourceFilter);
      if (aiFingerprint(items, store.queueStats()).equals(aiPageFingerprint)) return;
      ScrollView scroll = (ScrollView) body.getParent();
      int y = scroll.getScrollY();
      renderAiInbox(aiSourceFilter, true);
      scroll.post(() -> scroll.scrollTo(0, y));
    } catch (Exception ignored) {
      /* Existing result stays visible until the next refresh. */
    }
  }

  private void resetAiSelectionPage() {
    aiMultiSelectMode = false;
    aiSelection.all(false);
    aiSelectionBoxes.clear();
    if (aiBulkBar != null) outer.removeView(aiBulkBar);
    aiBulkBar = null;
    aiBulkCount = null;
    aiBulkAll = aiBulkIgnore = aiBulkSave = aiBulkCancel = null;
    try {
      aiSelection.replaceVisible(new JSONArray());
    } catch (Exception impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private void addAiTypeBadge(LinearLayout view, JSONObject item) {
    TextView badge = text(AiSuggestionTypes.label(item), 14, AiSuggestionTypes.color(item), true);
    badge.setPadding(dp(10), dp(5), dp(10), dp(5));
    badge.setBackground(shape(AiSuggestionTypes.background(item), 8));
    badge.setContentDescription("建议类型：" + AiSuggestionTypes.label(item));
    view.addView(badge, new LinearLayout.LayoutParams(-2, -2));
  }

  private void renderAiSections(LinearLayout view, JSONArray items) throws Exception {
    LinearLayout row = new LinearLayout(this);
    for (String section : AiSuggestionTypes.SECTIONS) {
      Button choice =
          button(
              AiSuggestionTypes.sectionLabel(section),
              () -> changeAiSection(section),
              section.equals(aiSection));
      choice.setContentDescription(AiSuggestionTypes.sectionLabel(section) + "分区");
      choice.setSelected(section.equals(aiSection));
      addBulkAction(row, choice);
    }
    view.addView(row);
    gap(view, 8);
    view.addView(
        text(
            "记忆 "
                + AiSuggestionTypes.filter(items, "memory").length()
                + " 条 · 资料 "
                + AiSuggestionTypes.filter(items, "reference").length()
                + " 条 · 事项 "
                + AiSuggestionTypes.filter(items, "work").length()
                + " 条",
            13,
            MUTED,
            false));
  }

  private void changeAiSection(String section) {
    if (busy || section.equals(aiSection)) return;
    boolean hadSelection = aiSelection.count() > 0;
    aiSection = section;
    aiPageIndex = 0;
    renderAiInbox(aiSourceFilter, false);
    ((ScrollView) body.getParent()).scrollTo(0, 0);
    if (hadSelection) toast("已切换分区，原来的勾选已清空");
  }

  private void toggleAiMultiSelect() {
    if (busy) return;
    if (!aiMultiSelectMode && aiSelection.visibleCount() == 0) {
      toast("当前没有可选择的建议");
      return;
    }
    aiMultiSelectMode = !aiMultiSelectMode;
    aiSelection.all(false);
    redrawAiSelection();
  }

  private void redrawAiSelection() {
    ScrollView scroll = (ScrollView) body.getParent();
    int y = scroll.getScrollY();
    renderAiInbox(aiSourceFilter, true);
    scroll.post(() -> scroll.scrollTo(0, y));
  }

  private void changeAiPage(int delta) {
    if (busy) return;
    aiPageIndex = Math.max(0, aiPageIndex + delta);
    renderAiInbox(aiSourceFilter, false);
    ((ScrollView) body.getParent()).scrollTo(0, 0);
  }

  private void createAiBulkBar() {
    if (aiBulkBar != null) outer.removeView(aiBulkBar);
    aiBulkBar = column();
    aiBulkBar.setPadding(dp(18), dp(8), dp(18), dp(8));
    aiBulkBar.setBackgroundColor(Color.WHITE);
    LinearLayout top = new LinearLayout(this);
    top.setGravity(Gravity.CENTER_VERTICAL);
    aiBulkCount = text("", 13, MUTED, false);
    top.addView(aiBulkCount, new LinearLayout.LayoutParams(0, -2, 1));
    aiBulkAll =
        button(
            "全选本页",
            () -> {
              aiSelection.all(aiSelection.count() != aiSelection.visibleCount());
              for (java.util.Map.Entry<String, CheckBox> entry : aiSelectionBoxes.entrySet())
                entry.getValue().setChecked(aiSelection.count() > 0);
              updateAiBulkBar();
            },
            false);
    top.addView(aiBulkAll, new LinearLayout.LayoutParams(-2, -2));
    aiBulkBar.addView(top);
    gap(aiBulkBar, 6);
    LinearLayout actions = new LinearLayout(this);
    aiBulkCancel = button("取消多选", this::toggleAiMultiSelect, false);
    aiBulkIgnore = button("批量忽略", this::confirmAiIgnore, false);
    aiBulkSave = button("核对并保存", this::previewAiSave, true);
    for (Button action : new Button[] {aiBulkCancel, aiBulkIgnore, aiBulkSave})
      addBulkAction(actions, action);
    aiBulkBar.addView(actions);
    outer.addView(aiBulkBar, outer.indexOfChild(nav), new LinearLayout.LayoutParams(-1, -2));
    updateAiBulkBar();
  }

  private void updateAiBulkBar() {
    if (!aiMultiSelectMode || aiBulkBar == null) return;
    aiBulkCount.setText(
        (busy ? "正在处理 · " : "")
            + "已选 "
            + aiSelection.count()
            + " / 本页 "
            + aiSelection.visibleCount()
            + " 条");
    for (CheckBox check : aiSelectionBoxes.values()) check.setEnabled(!busy);
    aiBulkAll.setText(
        aiSelection.count() > 0 && aiSelection.count() == aiSelection.visibleCount()
            ? "取消全选"
            : "全选本页");
    aiBulkAll.setEnabled(!busy && aiSelection.visibleCount() > 0);
    aiBulkCancel.setEnabled(!busy);
    for (Button action : new Button[] {aiBulkIgnore, aiBulkSave}) {
      action.setEnabled(!busy && aiSelection.count() > 0);
      action.setAlpha(action.isEnabled() ? 1 : 0.45f);
    }
  }

  private void confirmAiIgnore() {
    try {
      if (busy || aiSelection.count() == 0) return;
      JSONArray selected = aiSelection.snapshot();
      new AlertDialog.Builder(this)
          .setTitle("忽略 " + selected.length() + " 条建议？")
          .setMessage("选中的建议会从待确认列表收起，原始消息和已确认的记忆、事项继续保留。")
          .setNegativeButton("返回选择", null)
          .setPositiveButton("确认忽略", (dialog, which) -> performAiBatch(selected, false))
          .show();
    } catch (Exception failure) {
      toast("读取选择失败，请重新选择");
    }
  }

  private void previewAiSave() {
    try {
      if (busy || aiSelection.count() == 0) return;
      JSONArray selected = aiSelection.snapshot();
      async(
          () -> {
            try {
              return AiReviewBatch.preview(memories, selected, new ApiSettings(this).timezone());
            } catch (Exception failure) {
              return failure;
            }
          },
          result -> {
            if (result instanceof Exception) {
              toast(((Exception) result).getMessage());
              redrawAiSelection();
            } else showAiSavePreview((JSONArray) result);
          });
    } catch (Exception failure) {
      toast("读取选择失败，请重新选择");
    }
  }

  private void showAiSavePreview(JSONArray prepared) throws Exception {
    LinearLayout content = column();
    content.setPadding(dp(20), dp(12), dp(20), dp(16));
    content.addView(
        text("按以下内容批量保存。新事项只保存安排，提醒另行设置；变更已有事项时保留现有提醒，完成或取消会撤销对应提醒。", 14, MUTED, false));
    for (int i = 0; i < prepared.length(); i++) {
      JSONObject row = prepared.getJSONObject(i), proposal = row.getJSONObject("proposal");
      gap(content, 18);
      String kind = proposal.getString("kind"), op = proposal.optString("operation", "create");
      String action =
          op.equals("update")
              ? "变更"
              : op.equals("complete") ? "完成" : op.equals("cancel") ? "取消" : "保存";
      content.addView(text((i + 1) + ". " + proposal.getString("title"), 17, INK, true));
      content.addView(text(action + " · " + AiSuggestionTypes.label(proposal), 13, PURPLE, true));
      if (row.has("previous_title"))
        content.addView(text("对应已有事项：" + row.getString("previous_title"), 13, MUTED, false));
      content.addView(text(proposal.optString("reason"), 13, MUTED, false));
      if (row.has("edited")) {
        JSONObject edited = row.getJSONObject("edited");
        String zone = edited.getString("timezone");
        for (String key : new String[] {"due_at", "starts_at", "ends_at"}) {
          Long time = OrganizerStore.nullable(edited, key);
          if (time != null)
            content.addView(
                text(
                    (key.equals("due_at") ? "截止：" : key.equals("starts_at") ? "开始：" : "结束：")
                        + TimeRules.display(time, zone),
                    14,
                    PURPLE,
                    true));
        }
        String involvement = edited.optString("involvement");
        String state =
            op.equals("complete")
                ? "已完成"
                : op.equals("cancel")
                    ? "已取消"
                    : edited.optString("state").equals("waiting")
                        ? "等待别人"
                        : edited.optString("state").equals("blocked") ? "有阻塞" : "待执行";
        content.addView(
            text(
                "涉及："
                    + (involvement.equals("self")
                        ? "本人"
                        : involvement.equals("other") ? "其他人" : "待核实")
                    + " · "
                    + state,
                13,
                MUTED,
                false));
        if (edited.isNull("due_at") && edited.isNull("starts_at") && edited.isNull("ends_at"))
          content.addView(text("尚未确认具体时间", 13, MUTED, false));
        if (!proposal.optString("due_text", "").isEmpty())
          content.addView(text("时间原文：" + proposal.optString("due_text"), 13, MUTED, false));
        if (!edited.optString("location").isEmpty())
          content.addView(text("地点：" + edited.getString("location"), 13, MUTED, false));
        if (!edited.optString("details").isEmpty())
          content.addView(text("备注：" + edited.getString("details"), 13, MUTED, false));
        if (!row.isNull("remind_at"))
          content.addView(
              text("保留提醒：" + TimeRules.display(row.getLong("remind_at"), zone), 13, PURPLE, false));
      }
      JSONObject source = proposal.getJSONObject("source");
      content.addView(
          text(
              MessagePresentation.conversationTitle(source)
                  + " · "
                  + date(source.optLong("created_at")),
              12,
              MUTED,
              false));
      addButton(content, "查看建议出处", () -> aiDialog(proposal, false), false);
    }
    ScrollView scroll = new ScrollView(this);
    scroll.addView(content);
    new AlertDialog.Builder(this)
        .setTitle("核对 " + prepared.length() + " 条建议")
        .setView(scroll)
        .setNegativeButton("返回选择", null)
        .setPositiveButton(
            "确认保存 " + prepared.length() + " 条", (dialog, which) -> performAiBatch(prepared, true))
        .show();
  }

  private void performAiBatch(JSONArray batch, boolean save) {
    if (busy) return;
    async(
        () -> {
          try {
            int changed =
                save
                    ? AiReviewBatch.confirm(memories, batch, System.currentTimeMillis())
                    : AiReviewBatch.ignore(memories, batch);
            boolean reminderDeferred = false;
            if (save)
              try {
                ReminderScheduler.reconcile(this);
              } catch (Exception failure) {
                reminderDeferred = true;
              }
            return new JSONObject()
                .put("changed", changed)
                .put("reminder_deferred", reminderDeferred);
          } catch (Exception failure) {
            return failure;
          }
        },
        result -> {
          if (result instanceof Exception) {
            toast(((Exception) result).getMessage());
            redrawAiSelection();
            return;
          }
          JSONObject completed = (JSONObject) result;
          toast(
              "已"
                  + (save ? "保存 " : "忽略 ")
                  + completed.getInt("changed")
                  + " 条建议"
                  + (completed.optBoolean("reminder_deferred") ? "，提醒同步稍后重试" : ""));
          aiMultiSelectMode = false;
          renderAiInbox(aiSourceFilter, false);
        });
  }

  private void openProposal(JSONObject proposal) {
    startActivity(
        new Intent(this, OrganizerActivity.class).putExtra("proposal", proposal.toString()));
  }

  private void aiDialog(JSONObject item) {
    aiDialog(item, true);
  }

  private void aiDialog(JSONObject item, boolean editable) {
    try {
      JSONObject source = item.getJSONObject("source");
      JSONArray evidence = source.getJSONArray("evidence");
      LinearLayout view = column();
      view.setPadding(dp(20), dp(8), dp(20), 0);
      addAiTypeBadge(view, item);
      gap(view, 8);
      view.addView(text(item.getString("title"), 18, INK, true));
      gap(view, 8);
      view.addView(text(item.getString("reason"), 14, MUTED, false));
      String times = ProposalTimes.describe(item);
      if (!times.isEmpty()) {
        gap(view, 8);
        view.addView(text(times, 14, PURPLE, true));
      }
      String involvement = item.optString("involvement");
      gap(view, 8);
      view.addView(
          text(
              "模型判断涉及："
                  + (involvement.equals("self")
                      ? "本人"
                      : involvement.equals("other") ? "其他人" : "主体待核实"),
              13,
              PURPLE,
              false));
      for (int i = 0; i < evidence.length(); i++) {
        JSONObject ref = evidence.getJSONObject(i);
        gap(view, 16);
        view.addView(
            text(
                MessagePresentation.conversationTitle(ref)
                    + " · "
                    + date(ref.optLong("created_at")),
                12,
                MUTED,
                false));
        String sender = MessagePresentation.senderName(ref);
        if (!sender.isEmpty()) view.addView(text(sender, 13, PURPLE, true));
        TextView quote = text(MessagePresentation.body(ref), 14, INK, false);
        quote.setTextIsSelectable(true);
        view.addView(quote);
      }
      ScrollView scroll = new ScrollView(this);
      scroll.addView(view);
      String kind = item.optString("kind");
      String label = AiReviewBatch.memoryKind(item);
      AlertDialog.Builder dialog =
          new AlertDialog.Builder(this)
              .setTitle(AiSuggestionTypes.label(item) + "建议与出处")
              .setView(scroll)
              .setNegativeButton("关闭", null);
      if (editable)
        dialog
            .setPositiveButton(
                "核对并保存",
                (d, w) -> {
                  if (kind.equals("todo") || kind.equals("event")) openProposal(item);
                  else saveDialog(source, item.optString("title"), label, item);
                })
            .setNeutralButton(
                "忽略",
                (d, w) -> {
                  memories.reviewState(item.optString("review_ref"), "ignored");
                  aiInbox();
                });
      dialog.show();
    } catch (Exception e) {
      toast("读取建议出处失败");
    }
  }
}
