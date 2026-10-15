package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Namespaced synthetic connection; never configures the user's backend or reads WeChat. */
public final class BackendSmokeInstrumentation extends Instrumentation {
  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    start();
  }

  @Override
  public void onStart() {
    Context context = getTargetContext();
    Bundle output = new Bundle();
    boolean passed = false;
    MemoryStore memory = null;
    try {
      BackendSettings backend =
          new BackendSettings(context, "backend_fixture", "info-inbox-backend-fixture");
      backend.save("http://127.0.0.1:18631", "fixture-runtime-connection-token-0000000000");
      String connected = backend.client().connect();
      if (!backend.ready() || !connected.contains("宁宁"))
        throw new AssertionError("backend pairing");
      JSONObject source =
          new JSONObject()
              .put("account_id", "fixture-account")
              .put("talker", "fixture-room")
              .put("conversation_title", "合成会话")
              .put("msg_id", 51)
              .put("type", 1)
              .put("is_send", 1)
              .put("created_at", 1700000000000L)
              .put("content", "[fixture:event] 明天晚上开会");
      JSONObject result =
          backend
              .client()
              .analyze(
                  UUID.randomUUID().toString(),
                  new JSONArray().put(source),
                  new JSONArray(),
                  new JSONArray(),
                  "Asia/Shanghai");
      JSONObject event = result.getJSONArray("items").getJSONObject(0);
      if (!event.getString("kind").equals("event")
          || !event.getString("starts_at").equals("2026-10-13T10:00:00Z"))
        throw new AssertionError("backend event fields");
      JSONObject known =
          new JSONObject()
              .put("id", "work:fixture")
              .put("revision", 2)
              .put("kind", "todo")
              .put("title", "提交资料")
              .put("state", "open")
              .put("involvement", "self")
              .put("account_id", "fixture-account")
              .put("talker", "fixture-room");
      source.put("msg_id", 52).put("content", "[fixture:update] 资料改到周五");
      result =
          backend
              .client()
              .analyze(
                  UUID.randomUUID().toString(),
                  new JSONArray().put(source),
                  new JSONArray().put(known),
                  new JSONArray(),
                  "Asia/Shanghai");
      JSONObject update = result.getJSONArray("items").getJSONObject(0);
      if (!update.getString("operation").equals("update")
          || update.getInt("expected_revision") != 2
          || !update.getString("due_at").equals("2026-10-16T10:00:00Z"))
        throw new AssertionError("backend target and date");
      source.put("msg_id", 53).put("content", "[fixture:complete] 资料已收到");
      result =
          backend
              .client()
              .analyze(
                  UUID.randomUUID().toString(),
                  new JSONArray().put(source),
                  new JSONArray().put(known),
                  new JSONArray(),
                  "Asia/Shanghai");
      if (!result.getJSONArray("items").getJSONObject(0).getString("operation").equals("complete"))
        throw new AssertionError("completion proposal");
      source.put("msg_id", 54).put("content", "[fixture:event] 保留出处");
      context.deleteDatabase("backend_fixture.db");
      memory = new MemoryStore(context, "backend_fixture.db");
      OrganizerStore store = new OrganizerStore(memory);
      store.enqueue(new JSONArray().put(source));
      JSONObject batch = store.claim(System.currentTimeMillis());
      result =
          backend
              .client()
              .analyze(
                  batch.getString("id"),
                  batch.getJSONArray("sources"),
                  new JSONArray(),
                  new JSONArray(),
                  "Asia/Shanghai");
      store.finish(batch, result, System.currentTimeMillis());
      JSONObject proposal = memory.suggestions().getJSONObject(0);
      JSONObject edited =
          new JSONObject()
              .put("title", "合成会议")
              .put("timezone", "Asia/Shanghai")
              .put("involvement", "self")
              .put("state", "open")
              .put("starts_at", TimeRules.parseIso(proposal.getString("starts_at")))
              .put("ends_at", TimeRules.parseIso(proposal.getString("ends_at")))
              .put("due_at", JSONObject.NULL);
      String id = store.confirm(proposal, edited, null, System.currentTimeMillis());
      if (!store.work(id).getString("source_json").contains("保留出处")
          || store.queueStats().optInt("done") != 1)
        throw new AssertionError("backend to durable confirmation");
      output.putString(
          "result",
          "passed: Runtime authentication and owner session, backend model routing,"
              + " event/modify/complete proposals, source validation, durable queue and"
              + " confirmation; no provider key on mobile");
      passed = true;
    } catch (Throwable error) {
      output.putString(
          "result", "failed: " + error.getClass().getSimpleName() + " " + error.getMessage());
    } finally {
      if (memory != null) memory.close();
      context.deleteDatabase("backend_fixture.db");
      boolean cleaned =
          context
              .getSharedPreferences("backend_fixture", Context.MODE_PRIVATE)
              .edit()
              .clear()
              .commit();
      try {
        java.security.KeyStore keys = java.security.KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        keys.deleteEntry("info-inbox-backend-fixture");
      } catch (Exception error) {
        cleaned = false;
      }
      if (!cleaned) {
        passed = false;
        output.putString("result", "failed: fixture cleanup");
      }
    }
    finish(passed ? -1 : 0, output);
  }
}
