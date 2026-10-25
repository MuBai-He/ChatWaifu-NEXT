package com.mubai.infoinbox;

import android.app.Application;

/** All components share this process; recover the previous process's orphaned lease once. */
public final class InboxApplication extends Application {
  @Override
  public void onCreate() {
    super.onCreate();
    try (MemoryStore memory = new MemoryStore(this)) {
      new OrganizerStore(memory).recoverInterruptedRuns();
    } catch (RuntimeException unavailable) {
      // The bounded lease timeout remains the fallback when storage cannot be opened.
      getSharedPreferences("analysis", MODE_PRIVATE)
          .edit()
          .putString("last_error", "旧批次等待恢复，资料和进度仍保留")
          .apply();
    }
  }
}
