package com.mubai.infoinbox;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;

public final class AnalysisEngine {
  private static final Object INTAKE_LOCK = new Object();

  public static int intake(Context context) throws Exception {
    return intake(context, false);
  }

  public static int intake(Context context, boolean fullHistory) throws Exception {
    synchronized (INTAKE_LOCK) {
      try (MemoryStore memory = new MemoryStore(context)) {
        OrganizerStore store = new OrganizerStore(memory);
        JSONArray sources =
            new RootGateway(context).pendingSources(memory, fullHistory ? 0 : store.intakeFloor());
        store.enqueue(sources);
        return sources.length();
      }
    }
  }

  public static String run(Context context) throws Exception {
    return run(context, false);
  }

  public static String run(Context context, boolean fullHistory) throws Exception {
    JSONObject batch = null;
    try (MemoryStore memory = new MemoryStore(context)) {
      OrganizerStore store = new OrganizerStore(memory);
      ApiSettings settings = new ApiSettings(context);
      if (!AnalysisRouting.ready(context))
        throw new IllegalStateException(
            AnalysisRouting.standalone(context) ? "请先配置独立接口" : "请先连接宁宁后端");
      RootGateway root = new RootGateway(context);
      intake(context, fullHistory);
      batch = store.claim(System.currentTimeMillis(), fullHistory ? 0 : store.intakeFloor());
      if (batch == null) return "暂无可处理资料，或已有一批正在整理";
      JSONArray sources = batch.getJSONArray("sources");
      JSONObject response =
          AnalysisRouting.analyze(
              context,
              batch.getString("id"),
              sources,
              store.workItems(false),
              root.contextBefore(sources),
              new ConversationPolicy(memory).gate(store, batch));
      if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
      store.finish(batch, response, System.currentTimeMillis());
      context
          .getSharedPreferences("analysis", Context.MODE_PRIVATE)
          .edit()
          .putLong("last_success", System.currentTimeMillis())
          .putString("last_error", "")
          .apply();
      return "已整理 "
          + response.getJSONArray("processed_refs").length()
          + " 段资料，生成 "
          + response.getJSONArray("items").length()
          + " 条待确认建议";
    } catch (Exception e) {
      if (batch != null)
        try (MemoryStore memory = new MemoryStore(context)) {
          new OrganizerStore(memory)
              .fail(
                  batch.getString("id"),
                  System.currentTimeMillis(),
                  e instanceof InterruptedException || Thread.currentThread().isInterrupted());
        }
      context
          .getSharedPreferences("analysis", Context.MODE_PRIVATE)
          .edit()
          .putString("last_error", "整理未完成，资料和处理进度已保留")
          .apply();
      if (e instanceof InterruptedException) throw e;
      throw new IllegalStateException(
          AnalysisRouting.standalone(context)
              ? "独立接口整理未完成，资料和进度已保留。"
              : "宁宁整理暂未完成，请检查后端连接。资料和进度已保留。");
    }
  }
}
