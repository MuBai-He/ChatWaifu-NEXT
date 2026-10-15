package com.mubai.infoinbox;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import org.json.JSONArray;
import org.json.JSONObject;

/** One bounded sync cycle. The durable cursor only advances after local application succeeds. */
public final class KnowledgeSync {
  private static final Object LOCK = new Object();

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
    synchronized (LOCK) {
      BackendSettings backend = new BackendSettings(context);
      if (!enabled(context) || !backend.ready()) return;
      try (MemoryStore memory = new MemoryStore(context)) {
        KnowledgeStore store = new KnowledgeStore(memory, backend.base());
        RuntimeBackendClient client = backend.client();
        store.device();
        synchronized (ConversationPolicy.SEND_LOCK) {
          JSONArray exclusions = new ConversationPolicy(memory).list();
          for (int i = 0; i < exclusions.length(); i++)
            store.registerRoom(exclusions.getJSONObject(i));
          // Capture local edits before any remote version can replace their baseline.
          store.captureConfirmed();
        }
        for (int page = 0; page < 5; page++) {
          if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
          JSONObject pull = pullPage(context, client, backend, store);
          if (!pull.getBoolean("more")) break;
          if (page == 4)
            return; // Catch up fully before attempting a write against a stale baseline.
        }
        JSONArray policies = store.policies();
        if (policies.length() > 0) push(client, backend, store, new JSONArray(), policies);
        if (store.policies().length() > 0) return;
        boolean history =
            wifi(context)
                && (fullHistory
                    || context
                        .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
                        .getBoolean("history", true));
        JSONArray sources =
            new RootGateway(context).knowledgeCandidates(memory, store.space, history);
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
        pullPage(context, client, backend, store);
        JSONObject status =
            client.request("/v1/knowledge/status?session_id=" + backend.session(), null);
        store.putMeta("status", status.toString());
        store.meta("last_sync", System.currentTimeMillis());
        context
            .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
            .edit()
            .remove("error")
            .apply();
      }
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
    synchronized (ReminderScheduler.LOCK) {
      synchronized (ConversationPolicy.SEND_LOCK) {
        store.captureConfirmed();
        store.applyPull(pull);
      }
      ReminderScheduler.reconcile(context);
    }
    JSONArray applied = store.applied();
    client.request(
        "/v1/personal-information/sync/ack",
        new JSONObject()
            .put("session_id", backend.session())
            .put("device_id", store.device())
            .put("cursor", pull.getLong("cursor"))
            .put("applied", applied));
    store.acknowledged(applied);
    return pull;
  }

  private static void push(
      RuntimeBackendClient client,
      BackendSettings backend,
      KnowledgeStore store,
      JSONArray mutations,
      JSONArray policies)
      throws Exception {
    JSONObject body =
        new JSONObject()
            .put("session_id", backend.session())
            .put("device_id", store.device())
            .put("policy_epoch", store.epoch())
            .put("mutations", mutations)
            .put("policies", policies);
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
    store.db.beginTransaction();
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
