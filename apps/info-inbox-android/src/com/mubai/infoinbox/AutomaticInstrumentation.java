package com.mubai.infoinbox;

import android.Manifest;
import android.app.Activity;
import android.app.Instrumentation;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Switch;

/** Real SDK permission gate and the actual settings switch, without fixture message uploads. */
public final class AutomaticInstrumentation extends Instrumentation {
  private Bundle arguments;

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    arguments = args;
    start();
  }

  private static void check(boolean value, String name) {
    if (!value) throw new AssertionError(name);
  }

  private static Switch automatic(View view) {
    if (view instanceof Switch && "新消息自动整理".contentEquals(((Switch) view).getText()))
      return (Switch) view;
    if (view instanceof ViewGroup) {
      ViewGroup group = (ViewGroup) view;
      for (int i = 0; i < group.getChildCount(); i++) {
        Switch found = automatic(group.getChildAt(i));
        if (found != null) return found;
      }
    }
    return null;
  }

  @Override
  public void onStart() {
    Context context = getTargetContext();
    Bundle output = new Bundle();
    JobScheduler scheduler = context.getSystemService(JobScheduler.class);
    boolean passed = false;
    Activity activity = null;
    int id = 8899;
    try {
      boolean expectedMissing =
          arguments != null && "true".equals(arguments.getString("expect_missing_permission"));
      boolean granted =
          context.checkSelfPermission(Manifest.permission.ACCESS_NETWORK_STATE)
              == PackageManager.PERMISSION_GRANTED;
      boolean denied = false;
      try {
        int result =
            scheduler.schedule(
                new JobInfo.Builder(id, new ComponentName(context, AnalysisJob.class))
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setMinimumLatency(3600000L)
                    .setPersisted(true)
                    .build());
        check(result == JobScheduler.RESULT_SUCCESS, "network job accepted");
      } catch (SecurityException error) {
        denied = true;
      } finally {
        scheduler.cancel(id);
      }
      if (expectedMissing) {
        check(!granted && denied, "old APK reproduces network job SecurityException");
        output.putString("result", "reproduced: missing network state permission rejects job");
      } else {
        check(granted && !denied, "network state permission and JobScheduler gate");
        check(AnalysisRouting.ready(context), "backend ready");
        boolean original = new ApiSettings(context).automatic();
        activity =
            startActivitySync(
                new Intent(context, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra("tab", 4));
        Activity first = activity;
        runOnMainSync(
            () -> {
              Switch toggle = automatic(first.findViewById(android.R.id.content));
              check(toggle != null, "real automatic switch exists");
              if (toggle.isChecked()) toggle.performClick();
              check(!new ApiSettings(context).automatic(), "switch off persisted");
              toggle.performClick();
              check(new ApiSettings(context).automatic(), "switch on persisted");
            });
        waitForIdleSync();
        // Keep the user's setting after exercising both directions.
        if (!original) {
          runOnMainSync(() -> automatic(first.findViewById(android.R.id.content)).performClick());
        }
        runOnMainSync(first::finish);
        waitForIdleSync();
        activity =
            startActivitySync(
                new Intent(context, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra("tab", 4));
        Activity reopened = activity;
        runOnMainSync(
            () -> {
              Switch toggle = automatic(reopened.findViewById(android.R.id.content));
              check(
                  toggle != null && toggle.isChecked() == original,
                  "reopened UI preserves setting");
            });
        waitForIdleSync();
        output.putBoolean("network_state_permission", granted);
        output.putBoolean("automatic_setting", original);
        output.putBoolean("settings_switch_and_reopen", true);
        output.putString("result", "passed");
      }
      passed = true;
    } catch (Throwable error) {
      output.putString("result", "failed: " + error.getClass().getSimpleName());
      if (error instanceof AssertionError) output.putString("assertion", error.getMessage());
    } finally {
      scheduler.cancel(id);
      if (activity != null) {
        Activity last = activity;
        runOnMainSync(last::finish);
      }
    }
    finish(passed ? -1 : 0, output);
  }
}
