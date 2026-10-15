package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import org.json.JSONObject;

/** Disposable fixture validation. Never reads or changes the user's collection database. */
public final class SmokeInstrumentation extends Instrumentation {
  @Override
  public void onCreate(Bundle arguments) {
    super.onCreate(arguments);
    start();
  }

  @Override
  public void onStart() {
    Bundle output = new Bundle();
    Context context = getTargetContext();
    MemoryStore store = null;
    try {
      context.deleteDatabase("fixture_test.db");
      store = new MemoryStore(context, "fixture_test.db");
      JSONObject source =
          new JSONObject()
              .put("account_id", "fixture-account")
              .put("talker", "fixture-room")
              .put("msg_id", 17)
              .put("server_id", "9223372036854775806")
              .put("created_at", 1700000000000L)
              .put("content", "原文 'quote'\n第二行\u0000tail")
              .put("conversation_title", "测试会话")
              .put("is_send", 0);
      if (!store.state(source).equals("pending")) throw new AssertionError("initial state");
      store.save(source, "已确认的资料", "资料");
      if (store.list().length() != 1 || !store.state(source).equals("handled"))
        throw new AssertionError("confirmation");
      store.save(source, "修正后的标题", "待办");
      JSONObject item = store.list().getJSONObject(0);
      if (store.list().length() != 1 || !item.getString("title").equals("修正后的标题"))
        throw new AssertionError("idempotent editing");
      if (!item.getString("quote").equals(source.getString("content")))
        throw new AssertionError("quote fidelity");
      if (!item.getJSONObject("source").getString("server_id").equals("9223372036854775806"))
        throw new AssertionError("large identifier");
      if (!store
          .exportDocument()
          .getJSONArray("items")
          .getJSONObject(0)
          .getString("verification_state")
          .equals("user_confirmed")) throw new AssertionError("export provenance");
      store.remove(MemoryStore.reference(source));
      if (store.list().length() != 0 || !store.state(source).equals("pending"))
        throw new AssertionError("delete and reopen");
      store.mark(source, "ignored");
      if (!store.state(source).equals("ignored")) throw new AssertionError("ignore state");
      if (!OpenAiClient.normalizeBase("https://example.com/v1/chat/completions/")
          .equals("https://example.com/v1")) throw new AssertionError("URL normalization");
      boolean refused = false;
      try {
        OpenAiClient.normalizeBase("http://10.example.com");
      } catch (Exception expected) {
        refused = true;
      }
      if (!refused) throw new AssertionError("public cleartext refusal");
      ApiSettings config = new ApiSettings(context, "api_fixture", "info-inbox-fixture");
      config.save("http://127.0.0.1:18630/v1", "fixture-model", "test-key", true);
      if (!config.key().equals("test-key")
          || context
              .getSharedPreferences("api_fixture", Context.MODE_PRIVATE)
              .getString("encrypted_key", "")
              .contains("test-key")) throw new AssertionError("Keystore protection");
      OpenAiClient api = config.client();
      if (api.models().length() != 1) throw new AssertionError("models endpoint");
      org.json.JSONArray input = new org.json.JSONArray().put(source);
      JSONObject response = api.analyze(input);
      if (response.getJSONArray("items").length() != 1
          || response.getJSONArray("processed_refs").length() != 1)
        throw new AssertionError("compatibility response");
      store.acceptAnalysis(response, input);
      if (store.suggestions().length() != 1) throw new AssertionError("AI suggestions");
      JSONObject suggestion = store.suggestions().getJSONObject(0);
      store.save(suggestion.getJSONObject("source"), "人工确认的 AI 建议", "记忆");
      if (store.suggestions().length() != 0)
        throw new AssertionError("confirmation hides AI suggestion");
      JSONObject invalid =
          new JSONObject()
              .put("title", "bad")
              .put("kind", "memory")
              .put("importance", "high")
              .put("reason", "bad")
              .put("involvement", "self")
              .put("source_refs", new org.json.JSONArray().put("fabricated"));
      refused = false;
      try {
        OpenAiClient.validate(new org.json.JSONArray().put(invalid), new java.util.HashMap<>());
      } catch (Exception expected) {
        refused = true;
      }
      if (!refused) throw new AssertionError("fabricated reference refusal");
      output.putString(
          "result",
          "passed: memory confirmation and provenance, quote and 64-bit IDs, review states, HTTPS"
              + " policy, Keystore, OpenAI-compatible models/chat, validated references, AI result"
              + " confirmation");
      finish(ActivityResult.OK, output);
    } catch (Throwable e) {
      output.putString("result", "failed: " + e.getClass().getSimpleName() + " " + e.getMessage());
      finish(ActivityResult.FAIL, output);
    } finally {
      if (store != null) store.close();
      context.deleteDatabase("fixture_test.db");
      context.getSharedPreferences("api_fixture", Context.MODE_PRIVATE).edit().clear().commit();
      try {
        java.security.KeyStore keys = java.security.KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        keys.deleteEntry("info-inbox-fixture");
      } catch (Exception ignored) {
      }
    }
  }

  private static final class ActivityResult {
    static final int OK = -1, FAIL = 0;
  }
}
