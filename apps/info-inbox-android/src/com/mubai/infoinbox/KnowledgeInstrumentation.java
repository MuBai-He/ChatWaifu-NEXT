package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Debug-only owner acceptance. Outputs contain counts/booleans, never messages or credentials. */
public final class KnowledgeInstrumentation extends Instrumentation {
  private String mode;

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    mode = args == null ? "probe" : args.getString("mode", "probe");
    start();
  }

  @Override
  public void onStart() {
    Context context = getTargetContext();
    Bundle out = new Bundle();
    boolean passed = false;
    try (MemoryStore memory = new MemoryStore(context)) {
      BackendSettings backend = new BackendSettings(context);
      if (!backend.ready()) throw new IllegalStateException("backend_not_ready");
      KnowledgeStore store = new KnowledgeStore(memory, backend.base());
      if (mode.equals("enable")) {
        context
            .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
            .edit()
            .putBoolean("enabled", true)
            .commit();
        KnowledgeSync.run(context);
        KnowledgeJob.enqueue(context);
      } else if (mode.equals("sync")) KnowledgeSync.run(context);
      else if (mode.equals("view"))
        context.startActivity(
            new Intent(context, KnowledgeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
      else if (mode.equals("scope")) {
        JSONObject viewer =
            backend
                .client()
                .request(
                    "/v1/knowledge/viewer-session",
                    new JSONObject()
                        .put("session_id", backend.session())
                        .put("device_id", UUID.randomUUID().toString()));
        JSONObject provision =
            new JSONObject()
                .put("base_url", BackendSettings.normalize(backend.base()))
                .put("session_id", backend.session())
                .put("cookie", viewer.getString("cookie"));
        try (java.io.OutputStream file =
            context.openFileOutput("knowledge-viewer-acceptance.json", Context.MODE_PRIVATE)) {
          file.write(provision.toString().getBytes(StandardCharsets.UTF_8));
        }
        out.putBoolean("viewer_provisioned", true);
      } else if (mode.equals("reminder")) verifyReminder(context, memory, backend, store, out);
      out.putInt("schema", memory.getReadableDatabase().getVersion());
      out.putInt("excluded_conversations", new ConversationPolicy(memory).count());
      out.putBoolean("backend_ready", backend.ready());
      out.putBoolean("knowledge_enabled", KnowledgeSync.enabled(context));
      JSONObject status = new JSONObject(store.meta("status", "{}"));
      out.putInt("synchronized_records", status.optInt("records"));
      passed = true;
    } catch (Throwable failure) {
      out.putString("error_type", failure.getClass().getSimpleName());
      if (failure.getMessage() != null && failure.getMessage().matches("[a-z_]{1,80}"))
        out.putString("error_code", failure.getMessage());
    }
    out.putBoolean("passed", passed);
    finish(passed ? -1 : 0, out);
  }

  private void verifyReminder(
      Context context,
      MemoryStore memory,
      BackendSettings backend,
      KnowledgeStore store,
      Bundle out)
      throws Exception {
    String id = "acceptance:" + UUID.randomUUID();
    String webDevice = UUID.randomUUID().toString();
    out.putString("fixture_id", id);
    JSONObject draft =
        KnowledgeWire.draft(id, "todo", "fixture:knowledge-reminder", System.currentTimeMillis())
            .put("title", "验收：知识地图改期")
            .put("confirmed", true)
            .put("state", "open")
            .put("remind_at", System.currentTimeMillis() + 600000)
            .put("origin", new JSONObject().put("source_type", "acceptance_fixture"));
    int revision = 0;
    boolean created = false;
    try {
      revision = send(backend, webDevice, draft, 0, "upsert");
      created = true;
      KnowledgeSync.run(context);
      JSONObject first = new OrganizerStore(memory).reminder(id);
      if (first == null || first.getLong("due_at") != draft.getLong("remind_at"))
        throw new IllegalStateException("first_reminder_missing");
      draft.put("remind_at", draft.getLong("remind_at") + 600000);
      revision = send(backend, webDevice, draft, revision, "upsert");
      KnowledgeSync.run(context);
      JSONObject second = new OrganizerStore(memory).reminder(id);
      if (second.getLong("due_at") != draft.getLong("remind_at")
          || second.getInt("revision") <= first.getInt("revision"))
        throw new IllegalStateException("reminder_not_rescheduled");
      JSONObject remote =
          backend
              .client()
              .request("/v1/knowledge/records/" + id + "?session_id=" + backend.session(), null);
      if (remote.getJSONObject("sync").getInt("applied_devices") < 1)
        throw new IllegalStateException("applied_receipt_missing");
      draft.put("state", "cancelled");
      revision = send(backend, webDevice, draft, revision, "upsert");
      KnowledgeSync.run(context);
      if (!new OrganizerStore(memory).reminder(id).getString("state").equals("cancelled"))
        throw new IllegalStateException("old_reminder_active");
      out.putBoolean("web_change_applied", true);
      out.putBoolean("old_reminder_cancelled", true);
      out.putBoolean("phone_receipt_verified", true);
    } finally {
      if (created) {
        JSONObject latest =
            backend
                .client()
                .request("/v1/knowledge/records/" + id + "?session_id=" + backend.session(), null);
        send(backend, webDevice, draft, latest.getInt("revision"), "forget");
        KnowledgeSync.run(context);
      }
      if (new OrganizerStore(memory).work(id) != null)
        throw new IllegalStateException("fixture_not_forgotten");
      memory.getWritableDatabase().delete("reminders", "work_id=?", new String[] {id});
      memory.getWritableDatabase().delete("delivery_history", "work_id=?", new String[] {id});
    }
  }

  private int send(
      BackendSettings backend, String device, JSONObject draft, int revision, String action)
      throws Exception {
    JSONObject status =
        backend.client().request("/v1/knowledge/status?session_id=" + backend.session(), null);
    JSONObject response =
        backend
            .client()
            .request(
                "/v1/knowledge/commands",
                new JSONObject()
                    .put("session_id", backend.session())
                    .put("device_id", device)
                    .put("policy_epoch", status.getLong("policy_epoch"))
                    .put(
                        "mutations",
                        new JSONArray()
                            .put(
                                new JSONObject()
                                    .put("operation_id", UUID.randomUUID().toString())
                                    .put("expected_revision", revision)
                                    .put("action", action)
                                    .put("record", draft))));
    JSONObject receipt = response.getJSONArray("receipts").getJSONObject(0);
    if (!receipt.getString("status").equals("accepted"))
      throw new IllegalStateException("fixture_write_rejected");
    return receipt.getInt("revision");
  }
}
