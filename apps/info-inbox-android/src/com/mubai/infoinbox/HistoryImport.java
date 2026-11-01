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

  static long epoch(Context context) {
    return prefs(context).getLong("epoch", 0);
  }

  public static boolean oldestFirst(Context context) {
    return prefs(context).getBoolean("oldest_first", false);
  }

  public static synchronized void request(Context context) {
    request(context, oldestFirst(context));
  }

  public static synchronized void request(Context context, boolean oldestFirst) {
    prefs(context)
        .edit()
        .putBoolean("requested", true)
        .putBoolean("oldest_first", oldestFirst)
        .putLong("epoch", epoch(context) + (requested(context) ? 0 : 1))
        .putString("state", "running")
        .putString("message", "准备全量处理，原有进度会继续使用")
        .commit();
  }

  public static synchronized void pause(Context context, String message) {
    prefs(context)
        .edit()
        .putLong("epoch", epoch(context) + 1)
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

  static synchronized void phase(Context context, String phase, String message) {
    if (!requested(context)) return;
    prefs(context)
        .edit()
        .putString("phase", phase)
        .putString("message", message)
        .remove("last_error_code")
        .apply();
  }

  static synchronized void failure(Context context, Exception failure) {
    if (!requested(context)) return;
    String phase = prefs(context).getString("phase", "");
    String message =
        phase.equals("collection")
            ? "历史读取暂未完成，进度保留，稍后重试"
            : phase.equals("intake") ? "资料入队暂未完成，进度保留，稍后重试" : "本轮整理未完成，进度保留，稍后重试";
    prefs(context)
        .edit()
        .putString(
            "last_error_code",
            failure instanceof RootGateway.RootFailure
                ? ((RootGateway.RootFailure) failure).code
                : failure.getClass().getSimpleName())
        .putString("message", message)
        .commit();
  }

  static synchronized void progress(Context context, JSONObject stats, JSONObject knowledge) {
    if (!requested(context)) return;
    String message =
        (oldestFirst(context) ? "按历史顺序 · " : "新消息优先 · ")
            + "同时整理 "
            + stats.optInt("active_runs")
            + " 个会话（最多 "
            + OrganizerStore.MAX_CONCURRENCY
            + " 个） · AI 已整理 "
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
