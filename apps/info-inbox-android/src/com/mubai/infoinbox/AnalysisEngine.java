package com.mubai.infoinbox;

import android.content.Context;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.json.JSONArray;
import org.json.JSONObject;

public final class AnalysisEngine {
  private static final Object INTAKE_LOCK = new Object();

  public static int intake(Context context) throws Exception {
    return intake(context, false);
  }

  public static int intake(Context context, boolean fullHistory) throws Exception {
    if (RuntimeProcessing.owns(context)) {
      if (!RuntimeMessageService.requestSync(context)) KnowledgeJob.enqueueNow(context);
      return 0;
    }
    synchronized (INTAKE_LOCK) {
      try (MemoryStore memory = new MemoryStore(context)) {
        OrganizerStore store = new OrganizerStore(memory);
        JSONArray sources =
            new RootGateway(context)
                .pendingSources(
                    memory,
                    fullHistory ? 0 : store.intakeFloor(),
                    fullHistory && HistoryImport.oldestFirst(context));
        store.enqueue(sources);
        return sources.length();
      }
    }
  }

  public static String run(Context context) throws Exception {
    return run(context, false);
  }

  public static String run(Context context, boolean fullHistory) throws Exception {
    if (RuntimeProcessing.owns(context)) { KnowledgeJob.enqueueNow(context); return "已交给宁宁后端整理，可在持续协助中查看进度"; }
    intake(context, fullHistory);
    JSONObject result = runBatch(context, fullHistory);
    return result == null
        ? "暂无可处理资料，或同一会话正在整理"
        : "已整理 "
            + result.getJSONArray("processed_refs").length()
            + " 段资料，生成 "
            + result.getJSONArray("items").length()
            + " 条待确认建议";
  }

  /** One bounded wave; the store serializes each room and enforces the global lease cap. */
  public static int runWave(Context context, boolean fullHistory) throws Exception {
    if (RuntimeProcessing.owns(context)) { KnowledgeJob.enqueueNow(context); return 0; }
    intake(context, fullHistory);
    ExecutorService pool = Executors.newFixedThreadPool(OrganizerStore.MAX_CONCURRENCY);
    List<Future<JSONObject>> tasks = new ArrayList<>();
    ExecutorCompletionService<JSONObject> completed = new ExecutorCompletionService<>(pool);
    int limit = fullHistory ? 32 : 8, submitted = 0, processed = 0;
    long deadline = System.nanoTime() + (fullHistory ? 120_000_000_000L : 30_000_000_000L);
    Exception failure = null;
    try {
      for (int i = 0; i < OrganizerStore.MAX_CONCURRENCY; i++) {
        tasks.add(completed.submit(() -> runBatch(context, fullHistory)));
        submitted++;
      }
      while (!tasks.isEmpty()) {
        Future<JSONObject> task = completed.take();
        tasks.remove(task);
        boolean progressed = false;
        try {
          JSONObject result = task.get();
          if (result != null) {
            processed += result.getJSONArray("processed_refs").length();
            progressed = true;
          }
        } catch (ExecutionException error) {
          if (error.getCause() instanceof Exception) failure = (Exception) error.getCause();
          else throw error;
        }
        if (progressed
            && submitted < limit
            && System.nanoTime() < deadline
            && !Thread.currentThread().isInterrupted()) {
          tasks.add(completed.submit(() -> runBatch(context, fullHistory)));
          submitted++;
        }
      }
      if (processed == 0
          && failure != null
          && !(failure instanceof RuntimeBackendClient.AnalysisBusy)) throw failure;
      return processed;
    } catch (InterruptedException stopped) {
      Thread.currentThread().interrupt();
      throw stopped;
    } finally {
      for (Future<JSONObject> task : tasks) task.cancel(true);
      pool.shutdownNow();
    }
  }

  private static JSONObject runBatch(Context context, boolean fullHistory) throws Exception {
    JSONObject batch = null;
    final long epoch = HistoryImport.epoch(context);
    try (MemoryStore memory = new MemoryStore(context)) {
      OrganizerStore store = new OrganizerStore(memory);
      if (!AnalysisRouting.ready(context))
        throw new IllegalStateException(
            AnalysisRouting.standalone(context) ? "请先配置独立接口" : "请先连接宁宁后端");
      RootGateway root = new RootGateway(context);
      boolean oldest = fullHistory && HistoryImport.oldestFirst(context);
      for (int attempt = 0; attempt < 4; attempt++) {
        requireHistoryScope(context, fullHistory, epoch);
        batch =
            store.claim(System.currentTimeMillis(), fullHistory ? 0 : store.intakeFloor(), oldest);
        if (batch == null) return null;
        if(batch.optInt("priority",2)<2)oldest=false;
        if (!oldest) break;
        JSONArray earlier =
            root.earlierPendingSources(memory, batch.getJSONArray("sources").getJSONObject(0));
        if (earlier.length() == 0) break;
        // A partially populated queue cannot establish historical order by itself.
        store.yieldToEarlierSources(batch, earlier, System.currentTimeMillis());
        batch = null;
      }
      if (batch == null) return null;
      store.expandContext(
          batch,
          root.contextAround(batch.getJSONArray("sources"), memory),
          System.currentTimeMillis(),
          fullHistory ? 0 : store.intakeFloor());
      JSONArray background = store.analysisContext(batch);
      JSONObject response =
          AnalysisRouting.analyze(
              context,
              batch.getString("id"),
              batch.getJSONArray("sources"),
              store.workItems(false),
              background,
              historyGate(
                  context, fullHistory && oldest, epoch, new ConversationPolicy(memory).gate(store, batch)));
      requireHistoryScope(context, fullHistory && oldest, epoch);
      if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
      store.finish(batch, response, System.currentTimeMillis());
      try {
        if (fullHistory && HistoryImport.requested(context)) {
          KnowledgeStore knowledge =
              new KnowledgeStore(memory, new BackendSettings(context).base());
          HistoryImport.progress(
              context, store.queueStats(), new JSONObject(knowledge.meta("status", "{}")));
        }
      } catch (Exception unavailable) {
        /* Completed proposals do not depend on a status refresh. */
      }
      context
          .getSharedPreferences("analysis", Context.MODE_PRIVATE)
          .edit()
          .putLong("last_success", System.currentTimeMillis())
          .putString("last_error", "")
          .putString("last_error_code", "")
          .apply();
      return response;
    } catch (Exception error) {
      boolean stopped =
          error instanceof InterruptedException || Thread.currentThread().isInterrupted();
      boolean modelBusy = error instanceof RuntimeBackendClient.AnalysisBusy;
      boolean storageBusy = isStorageBusy(error);
      boolean busy = modelBusy || storageBusy;
      String failureCode = failureCode(error);
      if (batch != null)
        try (MemoryStore memory = new MemoryStore(context)) {
          new OrganizerStore(memory)
              .fail(batch.getString("id"), System.currentTimeMillis(), stopped || busy, failureCode);
        } catch (android.database.sqlite.SQLiteDatabaseLockedException stillBusy) {
          // The durable lease expires normally; a failed cleanup must not charge
          // a model failure or claim that its local result was committed.
          android.util.Log.w("InboxAnalysis", "failure_checkpoint_deferred storage_busy");
        } catch (android.database.sqlite.SQLiteTableLockedException stillBusy) {
          android.util.Log.w("InboxAnalysis", "failure_checkpoint_deferred storage_busy");
        }
      context
          .getSharedPreferences("analysis", Context.MODE_PRIVATE)
          .edit()
          .putString("last_error", modelBusy ? "其他会话正在整理，稍后继续" : failureMessage(failureCode))
          .putString("last_error_code", failureCode)
          .apply();
      if (stopped) {
        Thread.currentThread().interrupt();
        throw new InterruptedException();
      }
      if (busy) throw error;
      throw new IllegalStateException("本轮整理未完成，资料和进度已保留", error);
    }
  }

  static String failureCode(Exception error) {
    if (isStorageBusy(error)) return "storage_busy";
    if (error instanceof RuntimeBackendClient.AnalysisFailure)
      return ((RuntimeBackendClient.AnalysisFailure) error).code;
    if (error instanceof java.net.SocketTimeoutException) return "network_timeout";
    if (error instanceof java.io.IOException) return "connection_failed";
    if (error instanceof RuntimeBackendClient.StateConflict) return "source_changed";
    if (error instanceof org.json.JSONException || error instanceof IllegalArgumentException)
      return "result_validation_failed";
    return "analysis_failed";
  }

  private static boolean isStorageBusy(Exception error) {
    return error instanceof android.database.sqlite.SQLiteDatabaseLockedException
        || error instanceof android.database.sqlite.SQLiteTableLockedException;
  }

  public static String failureMessage(String code) {
    switch (code) {
      case "storage_busy": return "本地资料库正忙，稍后会自动重试，原文与处理进度保留。";
      case "model_output_invalid": return "模型结果未通过格式或出处校验，可重试失败资料。";
      case "model_unavailable": return "整理模型暂不可用，恢复后重试失败资料。";
      case "backend_unavailable": return "后端或网关暂不可用，恢复连接后重试。";
      case "network_timeout": return "连接超时，可在网络稳定后重试。";
      case "connection_failed": return "连接中断，可在网络恢复后重试。";
      case "analysis_request_invalid": return "资料批次未通过接入校验，需要重新核对来源。";
      case "source_changed": return "来源或版本发生变化，请刷新后重试。";
      case "result_validation_failed": return "返回结果未通过本机核对，可重试失败资料。";
      default: return "这条记录未记录具体原因，可重试失败资料，原有结果保留。";
    }
  }

  private static void requireHistoryScope(Context context, boolean full, long epoch)
      throws InterruptedException {
    if (full && (!HistoryImport.requested(context) || epoch != HistoryImport.epoch(context)))
      throw new InterruptedException();
  }

  private static AnalysisSendGate historyGate(
      Context context, boolean full, long epoch, AnalysisSendGate delegate) {
    return write -> {
      requireHistoryScope(context, full, epoch);
      if (full && !KnowledgeSync.wifi(context)) throw new InterruptedException();
      delegate.send(write);
    };
  }
}
