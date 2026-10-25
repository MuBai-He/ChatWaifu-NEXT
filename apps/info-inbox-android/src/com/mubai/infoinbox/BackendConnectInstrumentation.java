package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.json.JSONArray;
import org.json.JSONObject;

/** Owner-authorized connection setup; only synthetic records reach the real backend. */
public final class BackendConnectInstrumentation extends Instrumentation {
  private static final String DATABASE = "live_backend_acceptance_fixture.db";

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    start();
  }

  private static void check(boolean value, String name) {
    if (!value) throw new AssertionError(name);
  }

  @Override
  public void onStart() {
    Context context = getTargetContext();
    Bundle output = new Bundle();
    boolean passed = false;
    File provision = new File(context.getFilesDir(), "ningning-connection.tmp.json");
    MemoryStore memory = null;
    try {
      boolean automatic = new ApiSettings(context).automatic();
      BackendSettings backend = new BackendSettings(context);
      if (provision.exists()) {
        check(provision.isFile() && provision.length() <= 4096, "private connection provision");
        JSONObject connection =
            new JSONObject(
                new String(Files.readAllBytes(provision.toPath()), StandardCharsets.UTF_8));
        backend.save(connection.getString("base_url"), connection.getString("token"));
        if (!connection.optString("owner_session", "").isEmpty())
          backend.connected(connection.getString("owner_session"));
        check(provision.delete(), "plaintext provision removed");
      } else check(backend.ready(), "saved connection exists");
      backend.client().connect();
      check(backend.ready(), "backend connected");
      check(
          context
              .getSharedPreferences("analysis-route", Context.MODE_PRIVATE)
              .edit()
              .putBoolean("standalone", false)
              .commit(),
          "backend route persisted");
      check(AnalysisRouting.ready(context) && !AnalysisRouting.standalone(context), "app routing");
      check(
          new ApiSettings(context).automatic() == automatic,
          "automatic upload preference preserved");
      context.deleteDatabase(DATABASE);
      memory = new MemoryStore(context, DATABASE);
      OrganizerStore store = new OrganizerStore(memory);
      JSONObject source =
          new JSONObject()
              .put("account_id", "live-backend-synthetic-account")
              .put("talker", "live-backend-synthetic-room")
              .put("conversation_title", "合成资料验收")
              .put("msg_id", 1)
              .put("type", 1)
              .put("is_send", 1)
              .put("created_at", System.currentTimeMillis())
              .put("content", "我需要在2026年10月16日18:00（北京时间）之前提交项目清单。");
      store.enqueue(new JSONArray().put(source));
      JSONObject batch = store.claim(System.currentTimeMillis());
      Bundle progress = new Bundle();
      progress.putString("phase", "connected_waiting_for_real_backend_model");
      sendStatus(1, progress);
      long started = System.nanoTime();
      JSONObject result =
          AnalysisRouting.analyze(
              context,
              batch.getString("id"),
              batch.getJSONArray("sources"),
              new JSONArray(),
              new JSONArray());
      output.putLong("analysis_latency_ms", (System.nanoTime() - started) / 1000000L);
      JSONArray suggestions = result.getJSONArray("items");
      output.putInt("suggestions", suggestions.length());
      JSONObject todo = null;
      for (int i = 0; i < suggestions.length(); i++) {
        JSONObject candidate = suggestions.getJSONObject(i);
        if (candidate.getString("kind").equals("todo")) todo = candidate;
      }
      check(todo != null, "real model extracted explicit todo");
      output.putString("synthetic_involvement", todo.getString("involvement"));
      output.putString("synthetic_due_at", todo.optString("due_at", ""));
      check(todo.getString("involvement").equals("self"), "real model owner attribution");
      check(
          TimeRules.parseIso("2026-10-16T18:00:00+08:00")
              .equals(TimeRules.parseIso(todo.getString("due_at"))),
          "real model explicit deadline");
      store.finish(batch, result, System.currentTimeMillis());
      check(store.queueStats().optInt("done") == 1, "durable processed progress");
      check(memory.suggestions().length() >= 1, "durable pending proposals");
      check(store.workItems(true).length() == 0, "model proposal did not execute a task");
      JSONObject edited =
          new JSONObject()
              .put("title", "合成验收：提交项目清单")
              .put("timezone", "Asia/Shanghai")
              .put("involvement", "self")
              .put("state", "open")
              .put("due_at", TimeRules.parseIso(todo.getString("due_at")))
              .put("starts_at", JSONObject.NULL)
              .put("ends_at", JSONObject.NULL);
      String id = store.confirm(todo, edited, null, System.currentTimeMillis());
      check(store.work(id).getString("source_json").contains("项目清单"), "source provenance retained");
      output.putBoolean("backend_connected", backend.ready());
      output.putBoolean("provider_key_required", false);
      output.putBoolean("automatic_upload_enabled", automatic);
      output.putInt("suggestions", suggestions.length());
      output.putBoolean("explicit_deadline_validated", true);
      output.putBoolean("queue_and_confirmation_validated", true);
      passed = true;
    } catch (Throwable error) {
      output.putString(
          "failure",
          error.getClass().getSimpleName()
              + ": "
              + (error instanceof AssertionError
                  ? error.getMessage()
                  : "connection/synthetic acceptance"));
    } finally {
      if (provision.exists() && !provision.delete()) {
        passed = false;
        output.putString("cleanup_failure", "plaintext provision remains");
      }
      if (memory != null) memory.close();
      context.deleteDatabase(DATABASE);
    }
    output.putString("result", passed ? "passed" : "failed");
    finish(passed ? -1 : 0, output);
  }
}
