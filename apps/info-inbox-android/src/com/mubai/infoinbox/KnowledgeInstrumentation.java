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
    waitForIdleSync();
    Context context = getTargetContext();
    Bundle out = new Bundle();
    out.putInt("process_id", android.os.Process.myPid());
    boolean passed = false;
    if (mode.equals("harness")) {
      try (MemoryStore memory = new MemoryStore(context)) {
        verifyHarnessBridge(context, memory, out);
        passed = true;
      } catch (Throwable failure) {
        out.putString("error_type", failure.getClass().getSimpleName());
        JSONArray frames = new JSONArray();
        for (StackTraceElement frame : failure.getStackTrace()) {
          if (frame.getClassName().startsWith("com.mubai.infoinbox")) frames.put(frame.toString());
          if (frames.length() == 6) break;
        }
        out.putString("failure_frames", frames.toString());
        if (failure.getMessage() != null && failure.getMessage().matches("[a-z_]{1,80}"))
          out.putString("error_code", failure.getMessage());
      }
      out.putBoolean("passed", passed);
      finish(passed ? -1 : 0, out);
      return;
    }
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

  private void verifyHarnessBridge(Context context, MemoryStore memory, Bundle out)
      throws Exception {
    java.io.File file = new java.io.File(context.getFilesDir(), "harness-acceptance.json");
    JSONObject config =
        new JSONObject(
            new String(java.nio.file.Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
    file.delete();
    String base = config.getString("base"), marker = config.getString("marker");
    if (!(base.equals("http://127.0.0.1:18879")
            || base.equals("http://127.0.0.1:18880"))
        || !marker.startsWith("CW2 acceptance "))
      throw new IllegalArgumentException("invalid_acceptance_scope");
    String prefsName = "harness-acceptance-backend", alias = "info-inbox-harness-acceptance";
    BackendSettings backend = new BackendSettings(context, prefsName, alias);
    try {
      backend.save(base, config.getString("credential"));
      backend.connected(config.getString("session_id"));
      RuntimeBackendClient client = backend.client();
      String action = config.getString("action");
      if (action.equals("background_prepare")) {
        prepareBackgroundSync(context, config, out);
        return;
      }
      KnowledgeStore store = new KnowledgeStore(memory, base);
      store.putMeta("device", config.getString("device_id"));
      store.putMeta("processing_epoch", Integer.toString(config.optInt("processing_epoch")));
      if (action.startsWith("cold_")) {
        verifyColdSync(context, config, backend, client, action, out);
        return;
      }
      android.content.SharedPreferences journal =
          context.getSharedPreferences("harness-acceptance-restore", Context.MODE_PRIVATE);
      android.content.SharedPreferences sync =
          context.getSharedPreferences("knowledge", Context.MODE_PRIVATE);
      if (action.equals("pause")) {
        if (!journal.contains("enabled"))
          journal
              .edit()
              .putBoolean("was_set", sync.contains("enabled"))
              .putBoolean("enabled", sync.getBoolean("enabled", false))
              .commit();
        if (!sync.edit().putBoolean("enabled", false).commit())
          throw new IllegalStateException("fixture_sync_pause_failed");
        context.getSystemService(android.app.job.JobScheduler.class).cancel(8810);
        context.getSystemService(android.app.job.JobScheduler.class).cancel(8811);
        context.stopService(new Intent(context, RuntimeMessageService.class));
        out.putBoolean("original_sync_paused", true);
        return;
      }
      if (action.equals("restore")) {
        synchronized (ReminderScheduler.LOCK) {
          java.util.List<String> cleanupIds = new java.util.ArrayList<>();
          try (android.database.Cursor rows =
              store.db.rawQuery(
                  "SELECT local_id,payload FROM knowledge_shadow WHERE space=?",
                  new String[] {store.space})) {
            while (rows.moveToNext()) {
              JSONObject record = new JSONObject(rows.getString(1));
              String id = rows.getString(0);
              if (!record.optString("kind").matches("todo|event")
                  || !id.startsWith("harness:work:")) continue;
              cleanupIds.add(id);
            }
          }
          for (String id : cleanupIds) {
            JSONObject item = new OrganizerStore(memory).work(id);
            if (item != null) ReminderScheduler.changeState(context, memory, item, "cancelled");
            for (String table : new String[] {"reminders", "delivery_history", "work_changes"})
              store.db.delete(table, "work_id=?", new String[] {id});
            store.db.delete("work_items", "id=?", new String[] {id});
          }
          for (String table :
              new String[] {
                "knowledge_meta",
                "knowledge_shadow",
                "knowledge_outbox",
                "knowledge_sources",
                "knowledge_source_refs",
                "knowledge_rooms",
                "knowledge_priority",
                "runtime_activity_ack",
                "runtime_processed",
                "runtime_actions"
              }) store.db.delete(table, "space=?", new String[] {store.space});
        }
        if (journal.contains("enabled")) {
          android.content.SharedPreferences.Editor edit = sync.edit();
          if (journal.getBoolean("was_set", false))
            edit.putBoolean("enabled", journal.getBoolean("enabled", false));
          else edit.remove("enabled");
          if (!edit.commit()) throw new IllegalStateException("fixture_sync_restore_failed");
          journal.edit().clear().commit();
        }
        KnowledgeJob.enqueue(context);
        AnalysisJob.schedule(context, new ApiSettings(context).automatic());
        AnalysisJob.enqueueIntake(context, 1000L);
        out.putBoolean("original_sync_restored", true);
        return;
      }
      if (action.equals("room")) {
        JSONArray rooms = new RootGateway(context).conversations(memory, "filehelper", false);
        for (int i = 0; i < rooms.length(); i++) {
          JSONObject room = rooms.getJSONObject(i);
          if (!room.optString("talker").equals("filehelper")) continue;
          out.putString("room_id", KnowledgeWire.room(room));
          return;
        }
        throw new IllegalStateException("filehelper_not_found");
      }
      if (action.equals("activity")) {
        if (!store.meta("acceptance_activity_started", "").equals("true")) {
          store.putMeta("activity_cursor", Long.toString(config.getLong("activity_after")));
          store.putMeta("acceptance_activity_started", "true");
        }
        RuntimeProcessing.notifyChanges(context, store, client, backend);
        out.putBoolean("activity_processed", true);
        return;
      }
      // Match the production sync cycle: refresh policy and revisions before pushing.
      pullHarness(context, config, store, client, backend, marker);
      if (action.equals("source")) {
        RootGateway gateway = new RootGateway(context);
        JSONArray rooms = gateway.conversations(memory, "filehelper", false),
            sources = new JSONArray();
        for (int i = 0; i < rooms.length(); i++) {
          JSONObject room = rooms.getJSONObject(i);
          if (!room.optString("talker").equals("filehelper")) continue;
          JSONArray messages =
              gateway.messages(room.getString("account_id"), "filehelper", marker, 0);
          for (int j = 0; j < messages.length(); j++) {
            JSONObject message = messages.getJSONObject(j);
            if (message.optInt("is_send") == 1
                && message.getString("content").startsWith(marker)
                && message.getLong("created_at") >= config.getLong("since")) sources.put(message);
          }
        }
        if (sources.length() == 0 || sources.length() > 10)
          throw new IllegalStateException("synthetic_source_not_found");
        store.intake(sources);
        out.putString("room_id", KnowledgeWire.room(sources.getJSONObject(0)));
        out.putInt("source_count", sources.length());
      } else if (action.equals("receipts")) {
        try (android.database.Cursor rows =
            store.db.rawQuery(
                "SELECT id,local_id,payload FROM knowledge_shadow WHERE space=?",
                new String[] {store.space})) {
          while (rows.moveToNext()) {
            JSONObject record = new JSONObject(rows.getString(2));
            boolean keep = record.optString("title").contains(marker);
            JSONArray selectedIds = config.optJSONArray("record_ids");
            if (selectedIds != null)
              for (int j = 0; j < selectedIds.length(); j++)
                keep |= record.getString("record_id").equals(selectedIds.getString(j));
            if (!record.optString("kind").matches("todo|event")
                || !keep
                || rows.getString(1).isEmpty()) continue;
            JSONObject local = store.localRecord(rows.getString(1), record.getString("kind"));
            if (local != null) store.queue(local, rows.getString(1), "upsert");
          }
        }
      } else if (!action.equals("pull"))
        throw new IllegalArgumentException("invalid_acceptance_action");
      JSONArray mutations = store.mutations(false);
      if (mutations.length() > 0) {
        KnowledgeSync.push(client, backend, store, mutations, new JSONArray());
        out.putInt("mutations", mutations.length());
      }
      out.putInt(
          "downloaded_changes", pullHarness(context, config, store, client, backend, marker));
    } finally {
      context.getSharedPreferences(prefsName, Context.MODE_PRIVATE).edit().clear().commit();
      java.security.KeyStore keys = java.security.KeyStore.getInstance("AndroidKeyStore");
      keys.load(null);
      if (keys.containsAlias(alias)) keys.deleteEntry(alias);
    }
  }

  /** Only prepares an isolated fixture. The controller swaps files while the app is stopped;
   * the installed Release then runs its unchanged JobService and synchronization code. */
  private void prepareBackgroundSync(Context context, JSONObject config, Bundle out)
      throws Exception {
    String name = "harness-background-sync.db";
    if (!config.getString("base").equals("http://127.0.0.1:18880")
        || !config.getString("marker").startsWith("CW2 acceptance BG"))
      throw new IllegalArgumentException("invalid_background_scope");
    context.deleteDatabase(name);
    try (MemoryStore fixture = new MemoryStore(context, name)) {
      KnowledgeStore store = new KnowledgeStore(fixture, config.getString("base"));
      store.putMeta("device", config.getString("device_id"));
      if (config.optBoolean("timely_connection")) {
        store.putMeta("processing_epoch", Integer.toString(config.getInt("processing_epoch")));
        store.putMeta("joined:" + config.getString("runtime_room"),
            Integer.toString(config.getInt("processing_epoch")));
      }
      // Exclude the real archive locally. No actual message body is copied into this fixture.
      // The server independently rejects any non-synthetic record before persisting it.
      JSONArray rooms = new RootGateway(context).query(
          "SELECT DISTINCT account_id,talker FROM messages UNION SELECT account_id,username AS talker FROM contacts");
      store.db.beginTransaction();
      try {
        for (int i = 0; i < rooms.length(); i++) {
          JSONObject room = rooms.getJSONObject(i);
          store.db.execSQL("INSERT OR IGNORE INTO conversation_exclusions VALUES(?,?,?,?)",
              new Object[] {room.getString("account_id"), room.getString("talker"),
                  "验收期间排除原始会话", System.currentTimeMillis()});
          // This test is about transport and scheduling, not policy migration. Avoid sending
          // private archive metadata or policy changes to the isolated acceptance server.
          store.db.execSQL("INSERT OR IGNORE INTO knowledge_rooms(space,room_id,account_id,talker,label,revision,remote_excluded) VALUES(?,?,?,?,?,1,1)",
              new Object[] {store.space, KnowledgeWire.room(room), room.getString("account_id"),
                  room.getString("talker"), "验收期间排除原始会话"});
        }
        store.db.setTransactionSuccessful();
      } finally {
        store.db.endTransaction();
      }
      JSONArray sources = new JSONArray();
      long now = System.currentTimeMillis();
      int count = config.optInt("history_count", 1000);
      if (count < 1 || count > 2000) throw new IllegalArgumentException("invalid_fixture_size");
      for (int i = 0; i <= count; i++) {
        boolean live = i == count;
        sources.put(new JSONObject().put("account_id", "harness-background")
            .put("talker", "synthetic-background").put("msg_id", i + 1)
            .put("type", 1).put("is_send", 1)
            .put("created_at", live ? now : now - 40L * 86400000L - i * 1000L)
            .put("content", config.getString("marker") + (live ? " LIVE" : " HISTORY "+i))
            .put("_capture_mode", live ? "live" : "historical"));
      }
      store.intake(sources);
      store.db.rawQuery("PRAGMA wal_checkpoint(FULL)", null).close();
      out.putInt("synthetic_history_sources", count);
      out.putBoolean("live_source_queued", true);
      out.putBoolean("real_archive_excluded", true);
    }
    BackendSettings defaultBackend = new BackendSettings(context);
    defaultBackend.save(config.getString("base"), config.getString("credential"));
    defaultBackend.connected(config.getString("session_id"));
    context.getSharedPreferences("knowledge", Context.MODE_PRIVATE).edit()
        .putBoolean("enabled", true).putBoolean("history", true).commit();
    context.getSharedPreferences("runtime-processing", Context.MODE_PRIVATE).edit()
        .putBoolean("message_connection", false).commit();
    // The controller installs these staged, non-secret preferences only after it has
    // stopped the app and swapped the isolated database into place.
    context.getSharedPreferences("harness-background-runtime", Context.MODE_PRIVATE).edit()
        .clear().putString(config.getString("base"), "runtime")
        .putBoolean("message_connection", true).commit();
    out.putBoolean("background_fixture_prepared", true);
  }

  private void verifyColdSync(
      Context context,
      JSONObject config,
      BackendSettings backend,
      RuntimeBackendClient client,
      String action,
      Bundle out)
      throws Exception {
    String name = "harness-cold-sync.db";
    if (action.equals("cold_cleanup")) {
      context.deleteDatabase(name);
      out.putBoolean("isolated_database_removed", true);
      return;
    }
    try (MemoryStore memory = new MemoryStore(context, name)) {
      KnowledgeStore store = new KnowledgeStore(memory, backend.base());
      store.putMeta("device", config.getString("cold_device_id"));
      if (action.equals("cold_enqueue")) {
        boolean offline = false;
        try {
          client.request("/v1/runtime/health", null);
        } catch (java.io.IOException expected) {
          offline = true;
        }
        if (!offline) throw new IllegalStateException("cold_client_not_offline");
        RuntimeActions.enqueue(
            store,
            "/v1/agent/intents/" + config.getString("cold_intent_id"),
            new JSONObject()
                .put("expected_revision", config.getInt("cold_intent_revision"))
                .put("action", "pause"),
            "合成冷启动测试");
        JSONArray rows = RuntimeActions.list(store);
        if (rows.length() != 1) throw new IllegalStateException("cold_action_not_persisted");
        store.putMeta("cold_operation", rows.getJSONObject(0).getString("operation"));
        out.putBoolean("offline_action_persisted", true);
        return;
      }
      if (action.equals("cold_conflict")) {
        RuntimeActions.enqueue(
            store,
            "/v1/agent/intents/" + config.getString("cold_intent_id"),
            new JSONObject()
                .put("expected_revision", config.getInt("cold_intent_revision"))
                .put("action", "resume"),
            "合成旧修订测试");
        RuntimeActions.flush(store, client, backend);
        JSONArray rows = RuntimeActions.list(store);
        if (rows.length() != 1 || !rows.getJSONObject(0).getString("state").equals("conflict"))
          throw new IllegalStateException("cold_stale_action_not_blocked");
        out.putBoolean("stale_revision_retained_as_conflict", true);
        return;
      }
      if (!action.equals("cold_sync")) throw new IllegalArgumentException("invalid_cold_action");
      JSONArray pending = RuntimeActions.list(store);
      if (pending.length() != 1
          || !pending
              .getJSONObject(0)
              .getString("operation")
              .equals(store.meta("cold_operation", "")))
        throw new IllegalStateException("cold_action_lost_across_process");
      java.lang.reflect.Method pull =
          KnowledgeSync.class.getDeclaredMethod(
              "pullPage",
              Context.class,
              RuntimeBackendClient.class,
              BackendSettings.class,
              KnowledgeStore.class);
      pull.setAccessible(true);
      boolean more = true;
      int pages = 0;
      while (more && pages < 20) {
        JSONObject page;
        try {
          page = (JSONObject) pull.invoke(null, context, client, backend, store);
        } catch (java.lang.reflect.InvocationTargetException failure) {
          if (failure.getCause() instanceof Exception) throw (Exception) failure.getCause();
          throw failure;
        }
        more = page.getBoolean("more");
        pages++;
      }
      if (more) throw new IllegalStateException("cold_pull_page_budget");
      RuntimeActions.flush(store, client, backend);
      if (RuntimeActions.list(store).length() != 0)
        throw new IllegalStateException("cold_action_not_acknowledged");
      out.putBoolean("normal_unfiltered_pull", true);
      out.putBoolean("same_operation_acknowledged", true);
      out.putInt("pull_pages", pages);
      try (android.database.Cursor rows =
          store.db.rawQuery(
              "SELECT count(*) FROM knowledge_shadow WHERE space=?", new String[] {store.space})) {
        rows.moveToFirst();
        out.putInt("downloaded_records", rows.getInt(0));
      }
    }
  }

  private int pullHarness(
      Context context,
      JSONObject config,
      KnowledgeStore store,
      RuntimeBackendClient client,
      BackendSettings backend,
      String marker)
      throws Exception {
    JSONArray explicit = config.optJSONArray("record_ids");
    int downloaded = 0;
    for (int page = 0; page < 10; page++) {
      JSONObject pull =
          client.request(
              "/v1/personal-information/sync/pull?session_id="
                  + backend.session()
                  + "&device_id="
                  + store.device()
                  + "&after="
                  + store.meta("cursor", "0"),
              null);
      JSONArray changes = pull.getJSONArray("changes"), selected = new JSONArray();
      for (int i = 0; i < changes.length(); i++) {
        JSONObject change = changes.getJSONObject(i), value = change.getJSONObject("value");
        if (!change.getString("kind").equals("record")) continue;
        boolean keep =
            value.optString("title").contains(marker) || value.optString("text").startsWith(marker);
        if (explicit != null)
          for (int j = 0; j < explicit.length(); j++)
            keep |= value.getString("record_id").equals(explicit.getString(j));
        if (keep) selected.put(change);
      }
      downloaded += selected.length();
      synchronized (ReminderScheduler.LOCK) {
        store.applyPull(
            new JSONObject(pull.toString())
                .put("policies", new JSONArray())
                .put("changes", selected));
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
      if (!pull.getBoolean("more")) break;
    }
    // The acceptance stream is filtered. Fetch exact synthetic evidence dependencies
    // through the same owner API before echoing records; the normal client pulls all of them.
    java.util.Map<String, Long> dependencies = new java.util.LinkedHashMap<>();
    try (android.database.Cursor rows =
        store.db.rawQuery(
            "SELECT payload FROM knowledge_shadow WHERE space=?", new String[] {store.space})) {
      while (rows.moveToNext()) {
        JSONObject value = new JSONObject(rows.getString(0));
        if (!value.optString("kind").matches("event|todo")
            || !value.optString("title").contains(marker)) continue;
        JSONArray refs = value.optJSONArray("source_refs");
        if (refs == null) continue;
        for (int i = 0; i < refs.length(); i++) {
          JSONObject ref = refs.getJSONObject(i);
          dependencies.put(ref.getString("record_id"), ref.getLong("revision"));
        }
      }
    }
    if (dependencies.size() > 30) throw new IllegalStateException("fixture_evidence_budget");
    JSONArray missing = new JSONArray();
    for (java.util.Map.Entry<String, Long> entry : dependencies.entrySet()) {
      JSONObject current = store.shadow(entry.getKey());
      if (current != null && current.getLong("revision") == entry.getValue()) continue;
      JSONObject evidence =
          client.request(
              "/v1/knowledge/records/"
                  + java.net.URLEncoder.encode(entry.getKey(), "UTF-8")
                  + "?session_id="
                  + backend.session(),
              null);
      if (!evidence.getString("kind").equals("source")
          || !evidence.getString("room_id").equals(config.getString("room_id"))
          || evidence.getLong("revision") != entry.getValue()
          || evidence.optBoolean("deleted"))
        throw new IllegalStateException("fixture_evidence_not_current_or_scoped");
      missing.put(new JSONObject().put("kind", "record").put("value", evidence));
    }
    if (missing.length() > 0) {
      store.applyPull(
          new JSONObject()
              .put("policy_epoch", store.epoch())
              .put("cursor", Long.parseLong(store.meta("cursor", "0")))
              .put("policies", new JSONArray())
              .put("changes", missing));
      downloaded += missing.length();
    }
    return downloaded;
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
