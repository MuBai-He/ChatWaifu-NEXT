package com.mubai.infoinbox;

import android.app.*;
import android.os.*;
import android.view.View;
import android.widget.*;
import java.time.*;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import org.json.*;

/** One review surface shared by the suggestion queue and confirmed work. */
public final class SuggestionReview {
  private static final int INK = 0xFF242338, MUTED = 0xFF6B6A7F, PURPLE = 0xFF6152A5;
  private final Activity activity;
  private final MemoryStore memory;
  private final OrganizerStore store;
  private final ExecutorService worker;
  private final Handler main = new Handler(Looper.getMainLooper());
  private final Runnable onSaved;

  public SuggestionReview(
      Activity activity, MemoryStore memory, ExecutorService worker, Runnable onSaved) {
    this.activity = activity;
    this.memory = memory;
    this.store = new OrganizerStore(memory);
    this.worker = worker;
    this.onSaved = onSaved;
  }

  private int dp(int value) {
    return NativeUi.dp(activity, value);
  }

  private LinearLayout column() {
    return NativeUi.column(activity);
  }

  private TextView text(String value, int size, int color, boolean bold) {
    return NativeUi.text(activity, value, size, color, bold);
  }

  private void gap(LinearLayout view, int size) {
    view.addView(new android.view.View(activity), new LinearLayout.LayoutParams(1, dp(size)));
  }

  private void button(LinearLayout view, String label, Runnable action) {
    view.addView(NativeUi.button(activity, label, action, false));
  }

  private void toast(String message) {
    Toast.makeText(activity, message, Toast.LENGTH_LONG).show();
  }

  private static String operationLabel(String operation) {
    return operation.equals("complete") ? "完成" : operation.equals("cancel") ? "取消" : "变更";
  }

  public void show(JSONObject proposal) {
    if (proposal.optString("kind").matches("todo|event")) showWork(proposal);
    else showMemory(proposal);
  }

  private void showMemory(JSONObject proposal) {
    try {
      LinearLayout view = column();
      view.setPadding(dp(20), dp(8), dp(20), dp(16));
      EditText title = field(view, "确认内容", proposal.optString("title"));
      title.setSingleLine(false);
      title.setInputType(
          android.text.InputType.TYPE_CLASS_TEXT
              | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
      title.setMaxLines(3);
      EditText summary =
          field(
              view,
              "完整结论",
              proposal.optString("details").trim().isEmpty()
                  ? proposal.optString("title")
                  : proposal.optString("details"));
      summary.setInputType(
          android.text.InputType.TYPE_CLASS_TEXT
              | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
      summary.setMinLines(3);
      summary.setMaxLines(6);
      if (proposal.optString("details").isEmpty())
        view.addView(text("这条建议未附完整上下文，请核对原文后补全结论。", 13, MUTED, false));
      title.setSelectAllOnFocus(false);
      String[] kinds = {"记忆", "决定", "资料"};
      String selected = AiReviewBatch.memoryKind(proposal);
      Spinner kind =
          choices(view, "类型", kinds, selected.equals("决定") ? 1 : selected.equals("资料") ? 2 : 0);
      view.addView(text(proposal.optString("reason"), 14, MUTED, false));
      NativeUi.sources(activity, view, proposal.getJSONObject("source"));
      ScrollView scroll = new ScrollView(activity);
      scroll.addView(view);
      AlertDialog dialog =
          new AlertDialog.Builder(activity)
              .setTitle("核对建议")
              .setView(scroll)
              .setNegativeButton("返回建议", null)
              .setNeutralButton("忽略", null)
              .setPositiveButton("确认保存", null)
              .create();
      dialog.setOnShowListener(
          d -> {
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    v -> {
                      if (!dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled()) return;
                      final String confirmedTitle = title.getText().toString();
                      final String confirmedSummary = summary.getText().toString();
                      final String confirmedKind = kind.getSelectedItem().toString();
                      dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
                      dialog.getButton(AlertDialog.BUTTON_POSITIVE).setText("正在保存…");
                      dialog.setCancelable(false);
                      Runnable restore = NativeUi.freeze(view);
                      worker.execute(
                          () -> {
                            try {
                              store.confirmMemory(
                                  proposal, confirmedTitle, confirmedKind, confirmedSummary);
                              main.post(
                                  () -> {
                                    if (!activity.isFinishing()
                                        && !activity.isDestroyed()
                                        && dialog.isShowing()) {
                                      dialog.dismiss();
                                      onSaved.run();
                                    }
                                  });
                            } catch (Exception error) {
                              main.post(
                                  () -> {
                                    if (!activity.isFinishing()
                                        && !activity.isDestroyed()
                                        && dialog.isShowing()) {
                                      restore.run();
                                      dialog
                                          .getButton(AlertDialog.BUTTON_POSITIVE)
                                          .setEnabled(true);
                                      dialog.setCancelable(true);
                                      dialog.getButton(AlertDialog.BUTTON_POSITIVE).setText("确认保存");
                                      title.setError(error.getMessage());
                                    }
                                  });
                            }
                          });
                    });
            dialog
                .getButton(AlertDialog.BUTTON_NEUTRAL)
                .setOnClickListener(
                    v -> {
                      if (!dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled()) return;
                      ignore(proposal, dialog, view, title);
                    });
          });
      dialog.show();
      NativeUi.reviewWindow(activity, dialog, view);
    } catch (Exception error) {
      toast(error.getMessage() == null ? "建议无法读取，请刷新" : error.getMessage());
    }
  }

  private void ignore(JSONObject proposal, AlertDialog dialog, View form, EditText title) {
    dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
    dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setEnabled(false);
    dialog.setCancelable(false);
    Runnable restore = NativeUi.freeze(form);
    worker.execute(
        () -> {
          try {
            memory.reviewState(proposal.getString("review_ref"), "ignored");
            main.post(
                () -> {
                  if (!activity.isFinishing() && !activity.isDestroyed() && dialog.isShowing()) {
                    dialog.dismiss();
                    onSaved.run();
                  }
                });
          } catch (Exception failure) {
            main.post(
                () -> {
                  if (!activity.isFinishing() && !activity.isDestroyed() && dialog.isShowing()) {
                    restore.run();
                    dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                    dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setEnabled(true);
                    dialog.setCancelable(true);
                    title.setError(failure.getMessage());
                  }
                });
          }
        });
  }

  private EditText field(LinearLayout view, String label, String value) {
    gap(view, 10);
    view.addView(text(label, 13, MUTED, false));
    EditText edit = new EditText(activity);
    NativeUi.field(edit);
    edit.setText(value);
    edit.setSingleLine(true);
    edit.setSelectAllOnFocus(true);
    edit.setContentDescription(label);
    view.addView(edit);
    return edit;
  }

  private EditText timeField(LinearLayout view, String label, String value, String timezone) {
    EditText edit = field(view, label, value);
    edit.setHint("留空，或填写 2026-10-13 18:30");
    button(
        view,
        "选择" + label,
        () -> {
          ZonedDateTime now =
              Instant.ofEpochMilli(System.currentTimeMillis() + 3600000L)
                  .atZone(ZoneId.of(timezone));
          try {
            Long parsed = TimeRules.parseLocal(edit.getText().toString(), timezone);
            if (parsed != null) now = Instant.ofEpochMilli(parsed).atZone(ZoneId.of(timezone));
          } catch (Exception ignored) {
          }
          final ZonedDateTime initial = now;
          new DatePickerDialog(
                  activity,
                  (picker, year, month, day) ->
                      new TimePickerDialog(
                              activity,
                              (time, hour, minute) ->
                                  edit.setText(
                                      String.format(
                                          Locale.ROOT,
                                          "%04d-%02d-%02d %02d:%02d",
                                          year,
                                          month + 1,
                                          day,
                                          hour,
                                          minute)),
                              initial.getHour(),
                              initial.getMinute(),
                              true)
                          .show(),
                  initial.getYear(),
                  initial.getMonthValue() - 1,
                  initial.getDayOfMonth())
              .show();
        });
    return edit;
  }

  private Spinner choices(LinearLayout view, String label, String[] values, int selected) {
    gap(view, 10);
    view.addView(text(label, 13, MUTED, false));
    Spinner spinner = new Spinner(activity);
    spinner.setAdapter(
        new ArrayAdapter<>(activity, android.R.layout.simple_spinner_dropdown_item, values));
    spinner.setSelection(selected);
    view.addView(spinner);
    return spinner;
  }

  private Long proposedTime(JSONObject proposal, JSONObject old, String key) throws Exception {
    if (proposal.has(key) && !proposal.isNull(key))
      return TimeRules.parseIso(proposal.getString(key));
    return old == null ? null : OrganizerStore.nullable(old, key);
  }

  public void showWork(JSONObject proposal) {
    try {
      String operation = proposal.optString("operation", "create");
      JSONObject old =
          operation.equals("create") ? null : store.work(proposal.getString("target_id"));
      String timezone =
          old == null
              ? proposal.optString("timezone", new ApiSettings(activity).timezone())
              : old.getString("timezone");
      boolean event = proposal.getString("kind").equals("event"),
          terminal = operation.matches("complete|cancel");
      LinearLayout view = column();
      view.setPadding(dp(20), dp(6), dp(20), dp(16));
      final boolean historical = ProposalRelevance.needsHistoricalReview(proposal);
      if (historical)
        view.addView(text(proposal.optString("temporal_reason")
            + " 确认仍需处理后才会建立当前事项；系统没有推断它尚未完成。", 14, MUTED, true));
      view.addView(
          text(terminal ? "核对对应事项与新消息，确认后更新状态。" : "核对负责人、时间和原消息。提醒时间留空时只保存事项。", 14, MUTED, false));
      EditText title = field(view, "事项标题", proposal.getString("title"));
      title.setSingleLine(false);
      title.setInputType(
          android.text.InputType.TYPE_CLASS_TEXT
              | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
      title.setMaxLines(3);
      title.setSelectAllOnFocus(false);
      String involvement =
          old == null ? proposal.optString("involvement", "unclear") : old.getString("involvement");
      Spinner who =
          choices(
              view,
              "涉及谁",
              new String[] {"本人", "其他人", "尚不明确"},
              involvement.equals("self") ? 0 : involvement.equals("other") ? 1 : 2);
      String originalState =
          old == null ? proposal.optString("state", "open") : old.optString("state", "open");
      LinearLayout optional = NativeUi.disclosure(activity, view, "更多选项 · 状态、地点、备注与提醒");
      Spinner status =
          choices(
              optional,
              "当前状态",
              new String[] {"待执行", "等待别人", "有阻塞"},
              originalState.equals("waiting") ? 1 : originalState.equals("blocked") ? 2 : 0);
      EditText location =
          field(
              optional,
              "地点（可留空）",
              old == null ? proposal.optString("location", "") : old.optString("location", ""));
      EditText details =
          field(
              optional,
              "备注或等待对象（可留空）",
              old == null ? proposal.optString("details", "") : old.optString("details", ""));
      details.setSingleLine(false);
      details.setInputType(
          android.text.InputType.TYPE_CLASS_TEXT
              | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
      details.setMaxLines(4);
      view.addView(text("时区：" + timezone, 12, MUTED, false));
      EditText first =
          timeField(
              view,
              event ? "开始时间" : "截止时间",
              TimeRules.display(
                  proposedTime(proposal, old, event ? "starts_at" : "due_at"), timezone),
              timezone);
      EditText planned =
          event
              ? null
              : timeField(
                  optional,
                  "计划开始（可留空）",
                  TimeRules.display(proposedTime(proposal, old, "starts_at"), timezone),
                  timezone);
      EditText last =
          timeField(
              event ? view : optional,
              event ? "结束时间" : "计划结束（可留空）",
              TimeRules.display(proposedTime(proposal, old, "ends_at"), timezone),
              timezone);
      JSONObject oldRule = old == null ? null : store.reminder(old.getString("id"));
      String preset =
          oldRule != null && oldRule.optString("state").equals("scheduled")
              ? TimeRules.display(oldRule.getLong("due_at"), timezone)
              : "";
      EditText reminder = timeField(optional, "提醒时间", preset, timezone);
      if (terminal) {
        title.setEnabled(false);
        who.setEnabled(false);
        status.setEnabled(false);
        first.setEnabled(false);
        reminder.setEnabled(false);
        if (last != null) last.setEnabled(false);
        if (planned != null) planned.setEnabled(false);
      }
      gap(view, 14);
      view.addView(text("原消息", 16, INK, true));
      NativeUi.sources(activity, view, proposal.getJSONObject("source"));
      android.view.View toggle = view.getChildAt(view.indexOfChild(optional) - 1);
      view.removeView(toggle);
      view.removeView(optional);
      view.addView(toggle);
      view.addView(optional);
      ScrollView scroll = new ScrollView(activity);
      scroll.addView(view);
      AlertDialog dialog =
          new AlertDialog.Builder(activity)
              .setTitle(terminal ? "确认" + operationLabel(operation) : event ? "确认日程" : "确认待办")
              .setView(scroll)
              .setNegativeButton("取消", null)
              .setPositiveButton(historical ? "确认仍需处理" : "确认保存", null)
              .create();
      dialog.setOnShowListener(
          d ->
              dialog
                  .getButton(AlertDialog.BUTTON_POSITIVE)
                  .setOnClickListener(
                      v -> {
                        try {
                          Long at = TimeRules.parseLocal(first.getText().toString(), timezone),
                              end =
                                  last == null
                                      ? null
                                      : TimeRules.parseLocal(last.getText().toString(), timezone);
                          Long begin =
                              event
                                  ? at
                                  : TimeRules.parseLocal(planned.getText().toString(), timezone);
                          TimeRules.validateRange(begin, end);
                          JSONObject edited =
                              new JSONObject()
                                  .put("title", title.getText().toString())
                                  .put("timezone", timezone)
                                  .put(
                                      "state",
                                      new String[] {"open", "waiting", "blocked"}
                                          [status.getSelectedItemPosition()])
                                  .put(
                                      "involvement",
                                      new String[] {"self", "other", "unclear"}
                                          [who.getSelectedItemPosition()])
                                  .put("location", location.getText().toString())
                                  .put("details", details.getText().toString())
                                  .put("due_at", event || at == null ? JSONObject.NULL : at)
                                  .put("starts_at", begin == null ? JSONObject.NULL : begin)
                                  .put("ends_at", end == null ? JSONObject.NULL : end);
                          Long remind =
                              terminal
                                  ? null
                                  : TimeRules.parseLocal(reminder.getText().toString(), timezone);
                          dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
                          dialog.getButton(AlertDialog.BUTTON_POSITIVE).setText("正在保存…");
                          dialog.setCancelable(false);
                          Runnable restore = NativeUi.freeze(view);
                          worker.execute(
                              () -> {
                                try {
                                  ReminderScheduler.confirm(
                                      activity, memory, proposal, edited, remind);
                                  main.post(
                                      () -> {
                                        if (!activity.isFinishing()
                                            && !activity.isDestroyed()
                                            && dialog.isShowing()) {
                                          dialog.dismiss();
                                          toast("已保存到本机。宁宁的后续处理和日历核验结果请查看行动记录。");
                                          onSaved.run();
                                        }
                                      });
                                } catch (Exception error) {
                                  main.post(
                                      () -> {
                                        if (!activity.isFinishing()
                                            && !activity.isDestroyed()
                                            && dialog.isShowing()) {
                                          dialog
                                              .getButton(AlertDialog.BUTTON_POSITIVE)
                                              .setEnabled(true);
                                          restore.run();
                                          dialog.setCancelable(true);
                                          dialog.getButton(AlertDialog.BUTTON_POSITIVE).setText(historical ? "确认仍需处理" : "确认保存");
                                          title.setError(error.getMessage());
                                        }
                                      });
                                }
                              });
                        } catch (Exception error) {
                          title.setError(
                              "请核对输入，时间格式为 年-月-日 时:分；"
                                  + (error.getMessage() == null ? "" : error.getMessage()));
                        }
                      }));
      if (proposal.has("review_ref"))
        dialog.setButton(AlertDialog.BUTTON_NEUTRAL, "忽略", (d, which) -> {});
      dialog.show();
      if (proposal.has("review_ref")) {
        dialog
            .getButton(AlertDialog.BUTTON_NEUTRAL)
            .setOnClickListener(
                v -> {
                  if (!dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled()) return;
                  ignore(proposal, dialog, view, title);
                });
      }
      NativeUi.reviewWindow(activity, dialog, view);
    } catch (Exception e) {
      toast(e.getMessage() == null ? "建议无法打开，请刷新" : e.getMessage());
    }
  }
}
