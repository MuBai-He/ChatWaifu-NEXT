package com.mubai.infoinbox;

import android.content.res.ColorStateList;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.RippleDrawable;
import android.view.Gravity;
import android.widget.Button;

/** Shared native action geometry; height follows wrapped text and touch targets stay >=48dp. */
public final class NativeUi {
  private NativeUi() {}

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
