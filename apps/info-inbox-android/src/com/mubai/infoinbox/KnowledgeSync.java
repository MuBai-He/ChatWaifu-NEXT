package com.mubai.infoinbox;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import org.json.JSONArray;
import org.json.JSONObject;

/** One bounded sync cycle. The durable cursor only advances after local application succeeds. */
public final class KnowledgeSync {
  private static final java.util.concurrent.locks.ReentrantLock LOCK =
      new java.util.concurrent.locks.ReentrantLock();

  private KnowledgeSync() {}

  public static boolean enabled(Context context) {
    return context
        .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
        .getBoolean("enabled", false);
  }

  static boolean wifi(Context context) {
    ConnectivityManager network = context.getSystemService(ConnectivityManager.class);
    NetworkCapabilities caps = network.getNetworkCapabilities(network.getActiveNetwork());
    return caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
  }

  public static void run(Context context) throws Exception {
    run(context, false);
  }

  public static void run(Context context, boolean fullHistory) throws Exception {
    runCycle(context, fullHistory);
  }

  /** True means a bounded cycle committed progress and still has work to drain. */
  static boolean runCycle(Context context, boolean fullHistory) throws Exception {
    LOCK.lockInterruptibly();
    try {
      BackendSettings backend = new BackendSettings(context);
      if (!enabled(context) || !backend.ready()) return false;
      try (MemoryStore memory = new MemoryStore(context)) {
        KnowledgeStore store = new KnowledgeStore(memory, backend.base());
        RuntimeBackendClient client = backend.client();
        store.device();
        if (Integer.parseInt(store.meta("processing_epoch", "0")) > 0) {
          JSONObject ownership = client.request("/v1/personal-information/processing?session_id="
              + backend.session(), null);
          RuntimeProcessing.rememberOwnership(context, store, backend, ownership.getJSONArray("items"));
          store.putMeta("owner_review_supported", Boolean.toString(ownership.optBoolean("owner_review_supported")));
        }
        synchronized (ConversationPolicy.SEND_LOCK) {
          // Capture local edits before any remote version can replace their baseline.
          store.captureLocalState();
        }
        for (int page = 0; page < 5; page++) {
          if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
          JSONObject pull = pullPage(context, client, backend, store);
          if (!pull.getBoolean("more")) break;
          if (page == 4)
            return true; // Catch up before writing against a stale baseline.
        }
        JSONArray policies = store.policies();
        if (policies.length() > 0) push(client, backend, store, new JSONArray(), policies);
        if (store.policies().length() > 0) return true;
        boolean history =
            wifi(context)
                && (fullHistory
                    || context
                        .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
                        .getBoolean("history", true));
        RootGateway root = new RootGateway(context);
        root.ensureCaptureMetadata();
        JSONArray sources = root.knowledgeCandidates(memory, store.space, history);
        synchronized (ConversationPolicy.SEND_LOCK) {
          store.intake(sources);
        }
        for (int batch = 0; batch < 3; batch++) {
          if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
          synchronized (ConversationPolicy.SEND_LOCK) {
            store.captureConfirmed();
          }
          JSONArray mutations = store.mutations(history);
          if (mutations.length() == 0) break;
          push(client, backend, store, mutations, new JSONArray());
        }
        boolean more = pullPage(context, client, backend, store).getBoolean("more");
        JSONObject capabilities=client.request("/v1/personal-information/status",null);
        if(capabilities.optBoolean("runtime_processing_supported"))RuntimeProcessing.synchronize(context,store,client,backend);
        JSONObject status =
            client.request("/v1/knowledge/status?session_id=" + backend.session(), null);
        store.putMeta("status", status.toString());
        store.meta("last_sync", System.currentTimeMillis());
        context
            .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
            .edit()
            .remove("error")
            .apply();
        return more || store.mutations(history).length() > 0;
      }
    } finally {
      LOCK.unlock();
    }
  }

  private static JSONObject pullPage(
      Context context, RuntimeBackendClient client, BackendSettings backend, KnowledgeStore store)
      throws Exception {
    JSONObject pull =
        client.request(
            "/v1/personal-information/sync/pull?session_id="
                + backend.session()
                + "&device_id="
                + store.device()
                + "&after="
                + store.meta("cursor", "0"),
            null);
    android.util.Log.i("InboxKnowledge", "pull_received changes="
        + pull.getJSONArray("changes").length());
    synchronized (ReminderScheduler.LOCK) {
      synchronized (ConversationPolicy.SEND_LOCK) {
        store.captureConfirmed();
        store.applyPull(pull);
      }
      ReminderScheduler.reconcile(context);
    }
    android.util.Log.i("InboxKnowledge", "pull_applied");
    JSONArray applied = store.applied();
    client.request(
        "/v1/personal-information/sync/ack",
        new JSONObject()
            .put("session_id", backend.session())
            .put("device_id", store.device())
            .put("cursor", pull.getLong("cursor"))
            .put("applied", applied));
    store.acknowledged(applied);
    android.util.Log.i("InboxKnowledge", "pull_acknowledged");
    return pull;
  }

  static void push(
      RuntimeBackendClient client,
      BackendSettings backend,
      KnowledgeStore store,
      JSONArray mutations,
      JSONArray policies)
      throws Exception {
    if (policies.length() > 0 || mutations.length() == 0)
      pushGroup(client, backend, store, new JSONArray(), policies, 0);
    java.util.LinkedHashMap<Integer,JSONArray> groups = new java.util.LinkedHashMap<>();
    for (int i=0;i<mutations.length();i++) {
      JSONObject mutation=mutations.getJSONObject(i),record=mutation.getJSONObject("record");
      int epoch=Integer.parseInt(store.meta("processing_epoch:"+record.getString("room_id"),"0"));
      JSONArray batch=groups.get(epoch);
      if(batch==null){batch=new JSONArray();groups.put(epoch,batch);}
      batch.put(mutation);
    }
    for(java.util.Map.Entry<Integer,JSONArray> group:groups.entrySet())
      pushGroup(client,backend,store,group.getValue(),new JSONArray(),group.getKey());
  }

  private static void pushGroup(RuntimeBackendClient client, BackendSettings backend,
      KnowledgeStore store, JSONArray mutations, JSONArray policies, int processingEpoch)
      throws Exception {
    JSONObject body =
        new JSONObject()
            .put("session_id", backend.session())
            .put("device_id", store.device())
            .put("policy_epoch", store.epoch())
            .put("mutations", mutations)
            .put("policies", policies);
    if(processingEpoch>0) body.put("device_label",android.os.Build.MANUFACTURER+" "+android.os.Build.MODEL).put("schema_version","1.1").put("processing_epoch",processingEpoch).put("ingestion_mode","historical");
    JSONObject response =
        client.request(
            "/v1/personal-information/sync/push",
            body,
            write -> {
              synchronized (ConversationPolicy.SEND_LOCK) {
                if (policies.length() > 0) {
                  write.run();
                  return;
                }
                if (store.policies().length() > 0)
                  throw new IllegalStateException("排除范围发生变化，等待重新同步");
                for (int i = 0; i < mutations.length(); i++) {
                  JSONObject record = mutations.getJSONObject(i).getJSONObject("record");
                  if (record.getString("kind").equals("source")
                      && store.excluded(record.getString("room_id"))
                      && !mutations.getJSONObject(i).getString("action").equals("forget"))
                    throw new IllegalStateException("该会话已排除，停止资料上传");
                }
                write.run();
              }
            });
    store.db.beginTransactionNonExclusive();
    try {
      JSONArray receipts = response.getJSONArray("receipts");
      for (int i = 0; i < receipts.length(); i++) store.receiveReceipt(receipts.getJSONObject(i));
      store.meta("epoch", response.getLong("policy_epoch"));
      // server_cursor is NOT a pull checkpoint: other devices may have written meanwhile.
      store.db.setTransactionSuccessful();
    } finally {
      store.db.endTransaction();
    }
  }
}
