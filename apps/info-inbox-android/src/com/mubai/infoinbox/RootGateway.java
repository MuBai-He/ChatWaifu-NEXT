package com.mubai.infoinbox;

import android.content.Context;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONObject;

/** Each command is bounded and executed off the UI thread. No secrets or message logs. */
public final class RootGateway {
  static final class RootFailure extends Exception {
    final String code;
    final int exitCode;

    RootFailure(int exitCode, String output) {
      super("操作失败，请检查 Root 授权和采集状态");
      this.code = failureCode(output);
      this.exitCode = exitCode;
    }
  }

  static String failureCode(String output) {
    if (output.contains("Permission denied") || output.contains("Operation not permitted"))
      return "permission_denied";
    if (output.contains("Another collector command")) return "collector_busy";
    if (output.contains("Process is still stopping")) return "collector_stopping";
    if (output.contains("A collection is still in progress")) return "collector_busy";
    if (output.contains("Collection failed")) return "source_read_failed";
    if (output.contains("Source database is unavailable")) return "source_unavailable";
    if (output.contains("Background process did not start")) return "collector_start_failed";
    if (output.contains("No such file")) return "dependency_missing";
    return "command_failed";
  }

  public static final String BASE = "/data/adb/wechat-collector";
  private static final String BOOT = "/data/adb/service.d/info-inbox-collector.sh";
  private final Context context;
  private Boolean captureMetadataSupported;

  public RootGateway(Context context) {
    this.context = context.getApplicationContext();
  }

  public String exec(String command) throws Exception {
    return exec(command, 45);
  }

  private String exec(String command, int timeoutSeconds) throws Exception {
    // Android app data isolation otherwise hides WeChat from an app-owned Root process.
    Process p = new ProcessBuilder("su", "-M", "-c", command).redirectErrorStream(true).start();
    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    Thread drain =
        new Thread(
            () -> {
              try (InputStream in = p.getInputStream()) {
                byte[] buffer = new byte[8192];
                int n;
                while ((n = in.read(buffer)) != -1) {
                  if (bytes.size() + n > 2_000_000) {
                    p.destroyForcibly();
                    break;
                  }
                  bytes.write(buffer, 0, n);
                }
              } catch (Exception ignored) {
                /* Process exit is checked by the caller. */
              }
            },
            "root-output");
    drain.start();
    if (!p.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
      p.destroyForcibly();
      drain.join(2000);
      throw new Exception("Root 请求超时，请在 KernelSU 中检查此应用的授权");
    }
    drain.join(2000);
    if (drain.isAlive()) throw new Exception("读取结果未完整返回，请重试");
    if (p.exitValue() != 0) throw new RootFailure(p.exitValue(), bytes.toString("UTF-8"));
    return bytes.toString("UTF-8").trim();
  }

  public JSONObject status() throws Exception {
    if (!"0".equals(exec("id -u"))) throw new Exception("此功能需要 Root 授权");
    String raw =
        exec(
            "if [ -f "
                + BASE
                + "/collector.sh ]; then /system/bin/sh "
                + BASE
                + "/collector.sh status; else echo not_installed; fi");
    JSONObject result = new JSONObject();
    result.put("installed", !raw.contains("not_installed"));
    result.put("running", raw.contains("running=true"));
    result.put("error", raw.contains("last_cycle_error=true"));
    result.put("healthy", raw.contains("healthy=true"));
    for (String line : raw.split("\n")) {
      if (line.trim().startsWith("{")) {
        JSONObject stats = new JSONObject(line.trim());
        for (java.util.Iterator<String> i = stats.keys(); i.hasNext(); ) {
          String key = i.next();
          result.put(key, stats.get(key));
        }
      }
    }
    result.put(
        "autostart", "yes".equals(exec("if [ -x " + BOOT + " ]; then echo yes; else echo no; fi")));
    return result;
  }

  private void installCollectorScript() throws Exception {
    File script = new File(context.getCacheDir(), "collector-history-update.sh");
    try (InputStream in = context.getAssets().open("collector.sh");
        FileOutputStream out = new FileOutputStream(script)) {
      byte[] bytes = new byte[8192];
      int n;
      while ((n = in.read(bytes)) != -1) out.write(bytes, 0, n);
    }
    exec(
        "cp '"
            + script.getAbsolutePath()
            + "' "
            + BASE
            + "/collector.sh.next && chmod 700 "
            + BASE
            + "/collector.sh.next && mv "
            + BASE
            + "/collector.sh.next "
            + BASE
            + "/collector.sh");
  }

  public void ensureCaptureMetadata() throws Exception {
    if (exec("grep -q 'CREATE TABLE IF NOT EXISTS message_capture' " + BASE
        + "/collector.sh && echo yes || echo no").equals("yes")) return;
    boolean running = status().optBoolean("running");
    if (running) control("stop");
    try {
      installCollectorScript();
      control("once");
    } finally { if (running) control("start"); }
  }

  public void prepareHistoryCollection() throws Exception {
    installCollectorScript();
    JSONObject state = status();
    if (state.optBoolean("running")) control("stop");
    try {
      control("rescan");
    } finally {
      boolean interrupted = Thread.interrupted();
      try {
        control("start");
      } finally {
        if (interrupted) Thread.currentThread().interrupt();
      }
    }
  }

  public String control(String action) throws Exception {
    if (!action.equals("start")
        && !action.equals("stop")
        && !action.equals("once")
        && !action.equals("rescan")) throw new IllegalArgumentException();
    return exec(
        "/system/bin/sh " + BASE + "/collector.sh " + action, action.equals("rescan") ? 180 : 45);
  }

  public void ensureSignals() throws Exception {
    String installed =
        exec(
            "if [ -f "
                + BASE
                + "/collector.sh ]; then if grep -q 'com.mubai.infoinbox.COLLECTED' "
                + BASE
                + "/collector.sh; then echo ready; else echo upgrade; fi; else echo absent; fi");
    if (!installed.equals("upgrade")) return;
    boolean resume = status().optBoolean("running");
    File source = new File(context.getCacheDir(), "collector-0.3.sh");
    try (InputStream in = context.getAssets().open("collector.sh");
        FileOutputStream out = new FileOutputStream(source)) {
      byte[] buffer = new byte[8192];
      int n;
      while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
    }
    try {
      exec(
          "if [ ! -f "
              + BASE
              + "/collector-before-0.3.sh ]; then cp "
              + BASE
              + "/collector.sh "
              + BASE
              + "/collector-before-0.3.sh; chmod 700 "
              + BASE
              + "/collector-before-0.3.sh; fi");
      if (resume) control("stop");
      exec(
          "cp "
              + SourceRules.shellQuote(source.getAbsolutePath())
              + " "
              + BASE
              + "/collector.next && chmod 700 "
              + BASE
              + "/collector.next && mv "
              + BASE
              + "/collector.next "
              + BASE
              + "/collector.sh");
    } finally {
      source.delete();
      if (resume) control("start");
    }
  }

  public JSONArray query(String sql) throws Exception {
    String command =
        "printf '%s\\n' "
            + SourceRules.shellQuote(".bail on\n.timeout 5000\n.mode json\n" + sql)
            + " | LD_LIBRARY_PATH="
            + BASE
            + "/lib "
            + BASE
            + "/bin/sqlcipher -readonly -batch -bail "
            + BASE
            + "/messages.db";
    String raw = exec(command);
    return raw.isEmpty() ? new JSONArray() : new JSONArray(raw);
  }

  private String attachAppState(MemoryStore store) {
    store.getReadableDatabase();
    return "ATTACH DATABASE "
        + SourceRules.sqlQuote(
            "file:" + context.getDatabasePath("information.db").getAbsolutePath() + "?mode=ro")
        + " AS appstate; ";
  }

  private String inboxScope(boolean excludedOnly) {
    return (excludedOnly ? "" : "NOT ")
        + "EXISTS(SELECT 1 FROM appstate.conversation_exclusions e WHERE e.account_id=m.account_id"
        + " AND e.talker=m.talker)";
  }

  public JSONArray conversations(MemoryStore store, String search, boolean excludedOnly)
      throws Exception {
    String filter =
        search.isEmpty()
            ? ""
            : " AND (m.talker LIKE '%'||"
                + SourceRules.sqlQuote(search)
                + "||'%' OR coalesce(c.remark,'') LIKE '%'||"
                + SourceRules.sqlQuote(search)
                + "||'%' OR coalesce(c.nickname,'') LIKE '%'||"
                + SourceRules.sqlQuote(search)
                + "||'%')";
    return query(
        attachAppState(store)
            + "SELECT"
            + " m.account_id,m.talker,coalesce(nullif(c.remark,''),nullif(c.nickname,''),m.talker)"
            + " AS title,count(*) AS message_count,max(m.created_at) AS latest_at FROM messages m"
            + " LEFT JOIN contacts c ON c.account_id=m.account_id AND c.username=m.talker WHERE "
            + inboxScope(excludedOnly)
            + filter
            + " GROUP BY m.account_id,m.talker ORDER BY latest_at DESC LIMIT 100;");
  }

  private String fields() {
    return "m.account_id,m.msg_id,m.server_id,m.type,m.is_send,m.created_at,m.talker,substr(m.content,1,20000)"
               + " AS content,CASE WHEN length(m.content)>20000 THEN 1 ELSE 0 END AS"
               + " quote_truncated,coalesce(nullif(c.remark,''),nullif(c.nickname,''),m.talker) AS"
               + " conversation_title,(SELECT"
               + " coalesce(nullif(sc.remark,''),nullif(sc.nickname,''),'') FROM contacts sc WHERE"
               + " sc.account_id=m.account_id AND sc.username=CASE WHEN m.talker LIKE '%@chatroom'"
               + " AND m.is_send=0 AND instr(m.content,':'||char(10))>0 THEN"
               + " substr(m.content,1,instr(m.content,':'||char(10))-1) ELSE '' END LIMIT 1) AS"
               + " sender_name";
  }

  private String captureFields() throws Exception {
    if(captureMetadataSupported==null)captureMetadataSupported=query(
        "SELECT 1 AS supported FROM sqlite_master WHERE type='table'"
        + " AND name='message_capture' LIMIT 1;").length()>0;
    boolean supported=captureMetadataSupported;
    return fields() + (supported
        ? ",coalesce((SELECT capture_mode FROM message_capture f WHERE f.account_id=m.account_id"
          + " AND f.msg_id=m.msg_id),'historical') AS _capture_mode,coalesce((SELECT captured_at"
          + " FROM message_capture f WHERE f.account_id=m.account_id AND f.msg_id=m.msg_id),"
          + "m.collected_at*1000) AS _captured_at"
        : ",'historical' AS _capture_mode,m.collected_at*1000 AS _captured_at");
  }

  private String currentCaptureSql() {
    return Boolean.TRUE.equals(captureMetadataSupported)
        ? "EXISTS(SELECT 1 FROM message_capture f WHERE f.account_id=m.account_id"
          + " AND f.msg_id=m.msg_id AND f.capture_mode IN ('live','delayed'))" : "0";
  }

  public JSONArray knowledgeCandidates(MemoryStore store, String space, boolean history)
      throws Exception {
    long floor = System.currentTimeMillis() - 30L * 86400000L;
    String confirmed =
        "EXISTS(SELECT 1 FROM appstate.knowledge_priority p WHERE p.space="
            + SourceRules.sqlQuote(space)
            + " AND p.account_id=m.account_id AND p.msg_id=m.msg_id)";
    String fullFields = captureFields().replace("substr(m.content,1,20000)", "m.content");
    String recent="(m.created_at>="+floor+" OR "+currentCaptureSql()+")";
    // Choose identities before projecting full text and sender/contact metadata.
    // Large restored histories must not sort hundreds of thousands of message bodies.
    return query(
        attachAppState(store)
            + "WITH chosen AS (SELECT m.account_id,m.msg_id,CASE WHEN "
            + confirmed
            + " THEN 0 WHEN "
            + recent
            + " THEN 1 ELSE 2 END AS priority"
            + " FROM messages m WHERE m.type=1 AND "
            + inboxScope(false)
            + (history ? "" : " AND ("+recent+" OR " + confirmed + ")")
            + " AND NOT EXISTS(SELECT 1 FROM appstate.knowledge_sources k WHERE k.space="
            + SourceRules.sqlQuote(space)
            + " AND k.account_id=m.account_id AND k.msg_id=m.msg_id AND k.content=m.content)"
            + " ORDER BY CASE WHEN "
            + confirmed
            + " THEN 0 WHEN "
            + recent
            + " THEN 1 ELSE 2 END,m.created_at DESC,m.account_id,m.msg_id DESC LIMIT 100),packed AS (SELECT q.*,"
            + "row_number() OVER (ORDER BY q.priority,m.created_at DESC,m.account_id,m.msg_id DESC) AS position,"
            + "sum(length(coalesce(m.content,''))) OVER (ORDER BY q.priority,m.created_at DESC,m.account_id,m.msg_id DESC) AS chars"
            + " FROM chosen q JOIN messages m ON m.account_id=q.account_id AND m.msg_id=q.msg_id) SELECT "
            + fullFields
            + " FROM packed q JOIN messages m ON m.account_id=q.account_id AND m.msg_id=q.msg_id"
            + " LEFT JOIN contacts c ON c.account_id=m.account_id AND c.username=m.talker"
            + " WHERE q.chars<=200000 OR q.position=1 ORDER BY q.priority,m.created_at DESC,m.account_id,m.msg_id DESC;");
  }

  /** Include known contacts without archived messages, so they can be excluded before intake. */
  public JSONArray policyConversations(String search) throws Exception {
    String filter =
        search.isEmpty()
            ? ""
            : " WHERE r.title LIKE '%'||"
                + SourceRules.sqlQuote(search)
                + "||'%' OR r.talker LIKE '%'||"
                + SourceRules.sqlQuote(search)
                + "||'%'";
    return query(
        "WITH rooms AS (SELECT c.account_id,c.username AS"
            + " talker,coalesce(nullif(c.remark,''),nullif(c.nickname,''),c.username) AS title FROM"
            + " contacts c UNION ALL SELECT DISTINCT m.account_id,m.talker,m.talker AS title FROM"
            + " messages m WHERE NOT EXISTS(SELECT 1 FROM contacts c WHERE"
            + " c.account_id=m.account_id AND c.username=m.talker)),latest AS (SELECT"
            + " account_id,talker,max(created_at) AS latest_at FROM messages GROUP BY"
            + " account_id,talker) SELECT r.account_id,r.talker,r.title,coalesce(l.latest_at,0) AS"
            + " latest_at FROM rooms r LEFT JOIN latest l ON l.account_id=r.account_id AND"
            + " l.talker=r.talker"
            + filter
            + " ORDER BY latest_at DESC,r.title,r.talker LIMIT 100;");
  }

  public JSONArray messages(String account, String talker, String search, int offset)
      throws Exception {
    if (offset < 0 || offset > 1_000_000) throw new IllegalArgumentException();
    String filter =
        "m.account_id="
            + SourceRules.sqlQuote(account)
            + " AND m.talker="
            + SourceRules.sqlQuote(talker);
    if (!search.isEmpty())
      filter += " AND m.content LIKE '%'||" + SourceRules.sqlQuote(search) + "||'%'";
    return query(
        "SELECT "
            + fields()
            + " FROM messages m LEFT JOIN contacts c ON c.account_id=m.account_id AND"
            + " c.username=m.talker WHERE "
            + filter
            + " ORDER BY m.created_at DESC,m.msg_id DESC LIMIT 50 OFFSET "
            + offset
            + ";");
  }

  public JSONArray candidates(MemoryStore store) throws Exception {
    return query(
        attachAppState(store)
            + "SELECT "
            + fields()
            + " FROM messages m LEFT JOIN contacts c ON c.account_id=m.account_id AND"
            + " c.username=m.talker WHERE "
            + inboxScope(false)
            + " AND m.type=1 AND m.created_at>=unixepoch('now','-7"
            + " days')*1000 AND (m.content LIKE '%记得%' OR m.content LIKE '%麻烦%' OR m.content LIKE"
            + " '%需要你%' OR m.content LIKE '%请你%' OR m.content LIKE '%帮我%' OR m.content LIKE '%提交%'"
            + " OR m.content LIKE '%确认一下%' OR m.content LIKE '%截止%' OR m.content LIKE '%到期%' OR"
            + " m.content LIKE '%约定%' OR m.content LIKE '%会议%' OR m.content LIKE '%面试%' OR"
            + " m.content LIKE '%验收%' OR m.content LIKE '%待办%' OR m.content LIKE '%提醒%' OR"
            + " m.content LIKE '%决定%' OR m.content LIKE '%确定了%' OR m.content LIKE '%定下来%' OR"
            + " m.content LIKE '%以后%' OR m.content LIKE '%一直%' OR m.content LIKE '%习惯%' OR"
            + " m.content LIKE '%喜欢%' OR m.content LIKE '%偏好%' OR m.content LIKE '%地址是%' OR"
            + " m.content LIKE '%账号是%' OR m.content LIKE '%联系方式%') ORDER BY m.created_at"
            + " DESC,m.msg_id DESC LIMIT 100;");
  }

  public JSONArray analysisBatch(MemoryStore store) throws Exception {
    store.getWritableDatabase();
    String state =
        "file:" + context.getDatabasePath("information.db").getAbsolutePath() + "?mode=ro";
    return query(
        "ATTACH DATABASE "
            + SourceRules.sqlQuote(state)
            + " AS appstate; SELECT "
            + fields()
            + " FROM messages m LEFT JOIN contacts c ON c.account_id=m.account_id AND"
            + " c.username=m.talker WHERE m.type=1 AND m.created_at>=unixepoch('now','-7"
            + " days')*1000 AND NOT EXISTS(SELECT 1 FROM appstate.conversation_exclusions e WHERE"
            + " e.account_id=m.account_id AND e.talker=m.talker) AND NOT EXISTS(SELECT 1 FROM"
            + " appstate.ai_processed p WHERE p.account_id=m.account_id AND p.msg_id=m.msg_id)"
            + " ORDER BY m.created_at,m.msg_id LIMIT 30;");
  }

  public JSONArray pendingSources(MemoryStore store, long floor) throws Exception {
    return pendingSources(store, floor, false);
  }

  public JSONArray pendingSources(MemoryStore store, long floor, boolean oldestFirst)
      throws Exception {
    return pendingSources(store, floor, oldestFirst, null);
  }

  public JSONArray earlierPendingSources(MemoryStore store, JSONObject anchor) throws Exception {
    return pendingSources(store, 0, true, anchor);
  }

  private JSONArray pendingSources(
      MemoryStore store, long floor, boolean oldestFirst, JSONObject anchor) throws Exception {
    String earlier =
        anchor == null
            ? ""
            : " AND m.account_id="
                + SourceRules.sqlQuote(anchor.getString("account_id"))
                + " AND m.talker="
                + SourceRules.sqlQuote(anchor.getString("talker"))
                + " AND (m.created_at<"
                + anchor.getLong("created_at")
                + " OR (m.created_at="
                + anchor.getLong("created_at")
                + " AND m.msg_id<"
                + anchor.getLong("msg_id")
                + "))";
    store.getWritableDatabase();
    String state =
        "file:" + context.getDatabasePath("information.db").getAbsolutePath() + "?mode=ro";
    String fields =
        captureFields().replace("substr(m.content,1,20000) AS content", "m.content AS content");
    return query(
        "ATTACH DATABASE "
            + SourceRules.sqlQuote(state)
            + " AS appstate; SELECT "
            + fields
            + " FROM messages m LEFT JOIN contacts c ON c.account_id=m.account_id AND"
            + " c.username=m.talker LEFT JOIN appstate.source_snapshots s ON"
            + " s.account_id=m.account_id AND s.msg_id=m.msg_id WHERE m.type=1 AND (m.created_at>="
            + floor
            + " OR " + currentCaptureSql() + ")"
            + earlier
            + " AND " + RuntimeProcessing.nativeScopeSql(store,"m.account_id","m.talker",true)
            + " AND NOT EXISTS(SELECT 1 FROM appstate.conversation_exclusions e WHERE"
            + " e.account_id=m.account_id AND e.talker=m.talker)"
            + " AND ((s.base_ref IS NULL AND NOT EXISTS(SELECT 1 FROM appstate.ai_processed p WHERE"
            + " p.account_id=m.account_id AND p.msg_id=m.msg_id)) OR (s.base_ref IS NOT NULL AND"
            + " coalesce(m.content,'') IS NOT s.content)) ORDER BY m.created_at "
            + (oldestFirst ? "ASC" : "DESC")
            + ",m.msg_id "
            + (oldestFirst ? "ASC" : "DESC")
            + " LIMIT "
            + (floor == 0 ? 100 : 20)
            + ";");
  }

  public JSONArray contextAround(JSONArray sources, MemoryStore memory) throws Exception {
    if (sources.length() == 0) return new JSONArray();
    JSONObject source = sources.getJSONObject(0);
    long earliest = Long.MAX_VALUE, latest = 0;
    for (int i = 0; i < sources.length(); i++) {
      JSONObject part = sources.getJSONObject(i);
      earliest = Math.min(earliest, part.getLong("created_at"));
      latest = Math.max(latest, part.getLong("created_at"));
    }
    long midpoint = earliest + (latest - earliest) / 2;
    String full = fields().replace("substr(m.content,1,20000)", "m.content");
    return query(
        attachAppState(memory)
            + "SELECT "
            + full
            + " FROM messages m LEFT JOIN contacts c ON c.account_id=m.account_id AND"
            + " c.username=m.talker WHERE m.type=1 AND m.account_id="
            + SourceRules.sqlQuote(source.getString("account_id"))
            + " AND m.talker="
            + SourceRules.sqlQuote(source.getString("talker"))
            + " AND m.created_at BETWEEN "
            + Math.max(0, earliest - 30L * 60000L)
            + " AND "
            + (latest + 30L * 60000L)
            + " AND NOT EXISTS(SELECT 1 FROM appstate.conversation_exclusions e WHERE"
            + " e.account_id=m.account_id AND e.talker=m.talker) ORDER BY CASE WHEN m.created_at"
            + " BETWEEN "
            + earliest
            + " AND "
            + latest
            + " THEN 0 ELSE 1 END,abs(m.created_at-"
            + midpoint
            + "),m.msg_id LIMIT 40;");
  }

  public void initialize() throws Exception {
    if (!"0".equals(exec("id -u"))) throw new Exception("需要 Root 授权");
    if ("yes"
        .equals(
            exec(
                "if [ -x "
                    + BASE
                    + "/bin/sqlcipher ] && [ -f "
                    + BASE
                    + "/config.sh ]; then echo yes; else echo no; fi"))) {
      ensureSignals();
      return;
    }
    File archive = new File(context.getCacheDir(), "collector-runtime.tar");
    try (InputStream in = context.getAssets().open("collector-runtime.tar");
        FileOutputStream out = new FileOutputStream(archive)) {
      byte[] buffer = new byte[8192];
      int n;
      while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
    }
    try {
      String stage = BASE + "/app-install";
      exec(
          "umask 077; mkdir -p "
              + stage
              + "; tar -xf "
              + SourceRules.shellQuote(archive.getAbsolutePath())
              + " -C "
              + stage
              + "; /system/bin/sh "
              + stage
              + "/install.sh");
    } finally {
      archive.delete();
    }
  }

  public void autostart(boolean enabled) throws Exception {
    if (!enabled) {
      exec("rm -f " + BOOT);
      return;
    }
    String script =
        "#!/system/bin/sh\numask 077\nBASE=/data/adb/wechat-collector\n"
            + "[ -f \"$BASE/config.sh\" ] || exit 0\n"
            + "/system/bin/sh \"$BASE/collector.sh\" start >\"$BASE/boot-start.log\" 2>&1\n";
    exec(
        "umask 077; mkdir -p /data/adb/service.d; printf '%s' "
            + SourceRules.shellQuote(script)
            + " > "
            + BOOT
            + "; chmod 700 "
            + BOOT);
  }
}
