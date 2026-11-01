package com.mubai.infoinbox;

import android.app.*;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.view.*;
import android.view.Gravity;
import android.widget.*;
import org.json.*;

/** Shared native action geometry; height follows wrapped text and touch targets stay >=48dp. */
public final class NativeUi {
  private NativeUi() {}

  public static final String[] NAV_LABELS = {"概览", "AI建议", "事项", "记忆", "设置"};

  public static int dp(Context context, int value) {
    return Math.round(value * context.getResources().getDisplayMetrics().density);
  }

  public static LinearLayout column(Context context) {
    LinearLayout view = new LinearLayout(context);
    view.setOrientation(LinearLayout.VERTICAL);
    return view;
  }

  public static TextView text(Context context, String value, int size, int color, boolean bold) {
    TextView view = new TextView(context);
    view.setText(value);
    view.setTextSize(size);
    view.setTextColor(color);
    view.setLineSpacing(dp(context, 3), 1);
    if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
    return view;
  }

  public static GradientDrawable shape(Context context, int color, int radius) {
    GradientDrawable drawable = new GradientDrawable();
    drawable.setColor(color);
    drawable.setCornerRadius(dp(context, radius));
    return drawable;
  }

  public static Button button(Context context, String label, Runnable click, boolean primary) {
    Button button = new Button(context);
    button.setText(label);
    button.setTextColor(primary ? 0xFFFFFFFF : 0xFF6152A5);
    button.setBackground(shape(context, primary ? 0xFF6152A5 : 0xFFF0EDF8, 12));
    action(button);
    button.setOnClickListener(v -> click.run());
    return button;
  }

  public static void toggle(Switch toggle) {
    toggle.setMinHeight(dp(toggle.getContext(), 48));
    toggle.setSwitchPadding(dp(toggle.getContext(), 8));
  }

  public static void field(EditText edit) {
    edit.setTextSize(16);
    edit.setTextColor(0xFF242338);
    edit.setMinHeight(dp(edit.getContext(), 48));
    edit.setPadding(
        dp(edit.getContext(), 4),
        dp(edit.getContext(), 10),
        dp(edit.getContext(), 4),
        dp(edit.getContext(), 10));
  }

  public static LinearLayout actions(Context context, LinearLayout parent, Button... buttons) {
    LinearLayout row = new LinearLayout(context);
    row.setGravity(Gravity.CENTER_VERTICAL);
    row.setBaselineAligned(false);
    for (int i = 0; i < buttons.length; i++) {
      LinearLayout.LayoutParams item = new LinearLayout.LayoutParams(0, -2, 1);
      if (i > 0) item.leftMargin = dp(context, 8);
      row.addView(buttons[i], item);
    }
    LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1, -2);
    layout.topMargin = dp(context, 8);
    parent.addView(row, layout);
    return row;
  }

  public static LinearLayout disclosure(Context context, LinearLayout parent, String label) {
    LinearLayout details = column(context);
    details.setVisibility(View.GONE);
    Button toggle =
        button(
            context,
            label,
            () ->
                details.setVisibility(
                    details.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE),
            false);
    toggle.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
    toggle.setBackground(shape(context, 0x00000000, 12));
    LinearLayout.LayoutParams toggleLayout = new LinearLayout.LayoutParams(-1, -2);
    toggleLayout.setMargins(0, dp(context, 8), 0, dp(context, 4));
    parent.addView(toggle, toggleLayout);
    parent.addView(details);
    return details;
  }

  public static void sources(Context context, LinearLayout parent, JSONObject source)
      throws Exception {
    parent.addView(text(context, "来源与对话上下文", 14, 0xFF6152A5, true));
    JSONArray background = source.optJSONArray("_prior_knowledge");
    if (background != null && background.length() > 0) {
      LinearLayout prior = disclosure(context, parent, "整理时参考的历史背景（" + background.length() + "）");
      for (int i = 0; i < background.length(); i++) {
        JSONObject item = background.getJSONObject(i);
        prior.addView(
            text(
                context,
                (item.optString("state").equals("confirmed") ? "已确认记忆" : "待核对历史摘要")
                    + " · "
                    + item.optString("summary"),
                13,
                0xFF6B6A7F,
                false));
      }
    }
    JSONArray localEvidence = source.optJSONArray("evidence");
    boolean hasText = !source.optString("content").isEmpty();
    if (source.has("_knowledge_id") && localEvidence == null) hasText = false;
    if (localEvidence != null)
      for (int i = 0; i < localEvidence.length(); i++)
        hasText |= !localEvidence.getJSONObject(i).optString("content").isEmpty();
    if (!hasText) {
      parent.addView(text(context, "原文尚未保存在本机，请在个人资料页核对引用版本。", 13, 0xFF6B6A7F, false));
      return;
    }
    if (source.optBoolean("_context_incomplete"))
      parent.addView(text(context, "上下文未完整覆盖，请留意缺失的前提或后续修正。", 13, 0xFF9A6130, false));
    parent.addView(
        text(
            context,
            MessagePresentation.conversationTitle(source)
                + " · "
                + TimeRules.display(
                    source.optLong("created_at"), new ApiSettings(context).timezone()),
            12,
            0xFF6B6A7F,
            false));
    JSONArray window = source.optJSONArray("evidence");
    StringBuilder excerpt = new StringBuilder();
    if (window != null)
      for (int i = 0; i < window.length() && i < 3; i++) {
        JSONObject part = window.getJSONObject(i);
        if (MessagePresentation.body(part).isEmpty()) continue;
        if (excerpt.length() > 0) excerpt.append("\n");
        String sender = MessagePresentation.senderName(part);
        excerpt
            .append(sender.isEmpty() ? MessagePresentation.conversationTitle(part) : sender)
            .append("：")
            .append(MessagePresentation.body(part));
      }
    TextView preview =
        text(
            context,
            excerpt.length() > 0 ? excerpt.toString() : MessagePresentation.body(source),
            14,
            0xFF242338,
            false);
    preview.setMaxLines(3);
    preview.setEllipsize(android.text.TextUtils.TruncateAt.END);
    parent.addView(preview);
    LinearLayout details = disclosure(context, parent, "展开完整原文");
    JSONArray evidence = source.optJSONArray("evidence");
    if (evidence == null) evidence = new JSONArray().put(source);
    for (int i = 0; i < evidence.length(); i++) {
      JSONObject part = evidence.getJSONObject(i);
      if (part.has("_supporting_evidence"))
        details.addView(
            text(
                context,
                part.optBoolean("_historical_knowledge")
                    ? "历史背景出处"
                    : part.optBoolean("_supporting_evidence") ? "直接依据" : "相邻消息",
                12,
                0xFF6152A5,
                true));
      details.addView(
          text(
              context,
              MessagePresentation.conversationTitle(part)
                  + " · "
                  + MessagePresentation.senderName(part)
                  + " · "
                  + TimeRules.display(
                      part.optLong("created_at"), new ApiSettings(context).timezone()),
              12,
              0xFF6B6A7F,
              false));
      if (part.has("segment"))
        details.addView(
            text(context, "原消息第 " + part.optInt("segment") + " 段", 12, 0xFF6B6A7F, false));
      if (!part.optString("_segment_prefix").isEmpty())
        details.addView(
            text(context, "分段前文：" + part.optString("_segment_prefix"), 12, 0xFF6B6A7F, false));
      String body = MessagePresentation.body(part);
      TextView quote =
          text(
              context,
              body.isEmpty() && part.optBoolean("_historical_knowledge")
                  ? "历史背景的原文请在个人资料页核对引用版本。"
                  : body,
              15,
              0xFF242338,
              false);
      quote.setTextIsSelectable(true);
      details.addView(quote);
    }
  }

  public static Runnable freeze(View view) {
    java.util.List<View> controls = new java.util.ArrayList<>();
    collectControls(view, controls);
    boolean[] enabled = new boolean[controls.size()];
    for (int i = 0; i < controls.size(); i++) {
      enabled[i] = controls.get(i).isEnabled();
      controls.get(i).setEnabled(false);
    }
    return () -> {
      for (int i = 0; i < controls.size(); i++) controls.get(i).setEnabled(enabled[i]);
    };
  }

  private static void collectControls(View view, java.util.List<View> controls) {
    if (view instanceof EditText || view instanceof Spinner || view instanceof Button)
      controls.add(view);
    if (view instanceof ViewGroup)
      for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++)
        collectControls(((ViewGroup) view).getChildAt(i), controls);
  }

  private static String fields(View view) {
    if (view instanceof EditText) return ((EditText) view).getText().toString() + "\u0000";
    if (view instanceof Spinner) return ((Spinner) view).getSelectedItemPosition() + "\u0000";
    StringBuilder value = new StringBuilder();
    if (view instanceof ViewGroup)
      for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++)
        value.append(fields(((ViewGroup) view).getChildAt(i)));
    return value.toString();
  }

  public static void reviewWindow(Activity activity, AlertDialog dialog, View form) {
    dialog.setCanceledOnTouchOutside(false);
    if (dialog.getWindow() != null) {
      dialog
          .getWindow()
          .setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
      dialog.getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
      dialog.getWindow().setBackgroundDrawableResource(android.R.color.white);
    }
    // A full-height Window does not make the framework's wrap-content alert panel fill it.
    // Give the existing custom-content panel the spare height, leaving actions at the bottom.
    View parentPanel =
        dialog.findViewById(activity.getResources().getIdentifier("parentPanel", "id", "android"));
    View customPanel =
        dialog.findViewById(activity.getResources().getIdentifier("customPanel", "id", "android"));
    View custom =
        dialog.findViewById(activity.getResources().getIdentifier("custom", "id", "android"));
    if (parentPanel != null && parentPanel.getLayoutParams() != null) {
      ViewGroup.LayoutParams layout = parentPanel.getLayoutParams();
      layout.height = -1;
      parentPanel.setLayoutParams(layout);
    }
    if (customPanel != null && customPanel.getLayoutParams() instanceof LinearLayout.LayoutParams) {
      LinearLayout.LayoutParams layout = (LinearLayout.LayoutParams) customPanel.getLayoutParams();
      layout.height = 0;
      layout.weight = 1;
      customPanel.setLayoutParams(layout);
    }
    if (custom != null && custom.getLayoutParams() != null) {
      ViewGroup.LayoutParams layout = custom.getLayoutParams();
      layout.height = -1;
      custom.setLayoutParams(layout);
    }
    if (form.getParent() instanceof ScrollView) {
      ScrollView scroll = (ScrollView) form.getParent();
      ViewGroup.LayoutParams layout = scroll.getLayoutParams();
      layout.height = -1;
      scroll.setLayoutParams(layout);
    }
    Button primary = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
    if (primary.getParent() instanceof LinearLayout) {
      LinearLayout row = (LinearLayout) primary.getParent();
      Button[] buttons = {
        dialog.getButton(AlertDialog.BUTTON_NEUTRAL),
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE),
        primary
      };
      row.removeAllViews();
      row.setOrientation(LinearLayout.HORIZONTAL);
      row.setBaselineAligned(false);
      row.setPadding(dp(activity, 16), dp(activity, 10), dp(activity, 16), dp(activity, 10));
      ViewGroup.LayoutParams rowLayout = row.getLayoutParams();
      rowLayout.width = -1;
      rowLayout.height = -2;
      row.setLayoutParams(rowLayout);
      for (Button button : buttons) {
        if (button == null || button.getVisibility() == View.GONE) continue;
        action(button);
        button.setTextColor(button == primary ? 0xFFFFFFFF : 0xFF6152A5);
        button.setBackground(shape(activity, button == primary ? 0xFF6152A5 : 0xFFF0EDF8, 12));
        LinearLayout.LayoutParams layout =
            new LinearLayout.LayoutParams(0, -2, button == primary ? 2 : 1);
        if (row.getChildCount() > 0) layout.leftMargin = dp(activity, 8);
        row.addView(button, layout);
      }
    }
    final String initial = fields(form);
    Runnable close =
        () -> {
          if (!dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled()) return;
          if (initial.equals(fields(form))) dialog.dismiss();
          else
            new AlertDialog.Builder(activity)
                .setTitle("修改尚未保存")
                .setPositiveButton("继续编辑", null)
                .setNegativeButton("放弃修改", (d, w) -> dialog.dismiss())
                .show();
        };
    dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener(v -> close.run());
    dialog.setOnKeyListener(
        (d, key, event) -> {
          if (key == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) {
            close.run();
            return true;
          }
          return key == KeyEvent.KEYCODE_BACK;
        });
  }

  private static int dp(Button button, int value) {
    return Math.round(value * button.getResources().getDisplayMetrics().density);
  }

  public static void action(Button button) {
    button.setAllCaps(false);
    button.setTextSize(14);
    button.setMinHeight(dp(button, 48));
    button.setMinimumHeight(dp(button, 48));
    button.setMinWidth(0);
    button.setMinimumWidth(0);
    button.setPadding(dp(button, 14), dp(button, 10), dp(button, 14), dp(button, 10));
    button.setSingleLine(false);
    button.setGravity(Gravity.CENTER);
    button.setStateListAnimator(null);
    button.setElevation(0);
    Drawable background = button.getBackground();
    button.setBackground(new RippleDrawable(ColorStateList.valueOf(0x206152A5), background, null));
  }
}
