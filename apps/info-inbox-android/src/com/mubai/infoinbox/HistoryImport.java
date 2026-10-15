package com.mubai.infoinbox;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;

/** Durable user intent. Source revisions and retries remain in the existing SQLite queues. */
public final class HistoryImport {
  private HistoryImport() {}

  private static SharedPreferences prefs(Context context) {
    return context.getSharedPreferences("history-import", Context.MODE_PRIVATE);
  }

  public static boolean requested(Context context) {
    return prefs(context).getBoolean("requested", false);
  }

  public static synchronized void request(Context context) {
    prefs(context)
        .edit()
        .putBoolean("requested", true)
        .putString("state", "running")
        .putString("message", "准备全量处理，原有进度会继续使用")
        .commit();
  }

  public static synchronized void pause(Context context, String message) {
    prefs(context)
        .edit()
        .putBoolean("requested", false)
        .putString("state", "paused")
        .putString("message", message)
        .commit();
  }

  static synchronized void defer(Context context, String message) {
    if (requested(context))
      prefs(context).edit().putString("state", "background").putString("message", message).commit();
  }

  static void maybeComplete(Context context) throws Exception {
    if (!requested(context) || !KnowledgeSync.wifi(context)) return;
    try (MemoryStore memory = new MemoryStore(context)) {
      OrganizerStore organizer = new OrganizerStore(memory);
      JSONObject stats = organizer.queueStats();
      if (stats.optInt("pending") > 0 || stats.optInt("leased") > 0 || stats.optInt("failed") > 0)
        return;
      KnowledgeStore knowledge = new KnowledgeStore(memory, new BackendSettings(context).base());
      if (knowledge.conflictCount() > 0
          || knowledge.policies().length() > 0
          || knowledge.mutations().length() > 0) return;
      RootGateway root = new RootGateway(context);
      if (root.pendingSources(memory, 0).length() == 0
          && root.knowledgeCandidates(memory, knowledge.space, true).length() == 0)
        complete(context);
    }
  }

  static synchronized void progress(Context context, JSONObject stats, JSONObject knowledge) {
    if (!requested(context)) return;
    String message =
        "AI 已整理 "
            + stats.optInt("done")
            + " 段，待整理 "
            + stats.optInt("pending")
            + " 段；知识库已同步 "
            + knowledge.optInt("records")
            + " 条，已建索引 "
            + knowledge.optInt("indexed_records")
            + " 条";
    prefs(context)
        .edit()
        .putString("state", "running")
        .putString("message", message)
        .remove("last_error_code")
        .commit();
  }

  static synchronized void waiting(Context context) {
    if (!requested(context)) return;
    prefs(context)
        .edit()
        .putString("state", "waiting_wifi")
        .putString("message", "等待 Wi-Fi，恢复连接后会继续处理")
        .commit();
  }

  static synchronized void complete(Context context) {
    if (!requested(context)) return;
    prefs(context)
        .edit()
        .putBoolean("requested", false)
        .putString("state", "done")
        .putString("message", "本机历史已提交并完成 AI 建议；后端继续建立知识图谱")
        .commit();
  }

  public static String status(Context context) {
    return prefs(context).getString("message", "尚未启动全量处理；新消息按原有设置整理");
  }

  static boolean drained(int newIntake, JSONObject stats, int uploadCandidates, int outbox) {
    return newIntake == 0
        && uploadCandidates == 0
        && outbox == 0
        && stats.optInt("pending") == 0
        && stats.optInt("leased") == 0
        && stats.optInt("failed") == 0;
  }
}
