package com.mubai.infoinbox;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import org.json.JSONArray;
import org.json.JSONObject;

/** Calls Ningning Runtime APIs; no provider endpoint, model or provider key is sent. */
public final class RuntimeBackendClient {
  private final BackendSettings settings;

  RuntimeBackendClient(BackendSettings settings) {
    this.settings = settings;
  }

  JSONObject request(String path, JSONObject body) throws Exception {
    return request(path, body, AnalysisSendGate.DIRECT);
  }

  JSONObject request(String path, JSONObject body, AnalysisSendGate gate) throws Exception {
    HttpURLConnection connection =
        (HttpURLConnection)
            new URL(BackendSettings.normalize(settings.base()) + path).openConnection();
    connection.setConnectTimeout(15000);
    connection.setReadTimeout(90000);
    connection.setInstanceFollowRedirects(false);
    connection.setRequestProperty("Authorization", "Bearer " + settings.credential());
    connection.setRequestProperty("Accept", "application/json");
    long deadline = System.nanoTime() + 120_000_000_000L;
    try {
      if (body != null) {
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        gate.send(
            () -> {
              try (java.io.OutputStream out = connection.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
              }
            });
      }
      int code = connection.getResponseCode();
      if (code == 404) throw new IllegalStateException("宁宁的信息接入功能尚未就绪，资料继续保存在本机");
      if (code == 401 || code == 403) throw new IllegalStateException("宁宁后端拒绝连接，请检查连接凭据");
      if (code == 503) throw new IllegalStateException("宁宁的整理服务暂不可用，请稍后重试");
      if (code < 200 || code >= 300)
        throw new IllegalStateException("宁宁后端返回 HTTP " + code + "，资料和进度已保留");
      try (InputStream in = connection.getInputStream();
          ByteArrayOutputStream data = new ByteArrayOutputStream()) {
        byte[] buffer = new byte[8192];
        int n;
        while ((n = in.read(buffer)) != -1) {
          if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
          if (System.nanoTime() > deadline || data.size() + n > 1_000_000)
            throw new IllegalStateException("宁宁后端响应超时或过大");
          data.write(buffer, 0, n);
        }
        return new JSONObject(data.toString("UTF-8"));
      }
    } finally {
      connection.disconnect();
    }
  }

  public String connect() throws Exception {
    JSONObject status = request("/v1/personal-information/status", null);
    if (!status.optString("schema_version").equals("1.1")
        || !status.optString("model_owner").equals("chatwaifu_runtime")
        || status.optBoolean("provider_api_key_required_on_client", true))
      throw new IllegalStateException("宁宁后端的信息协议不兼容");
    if (!status.optBoolean("model_ready"))
      throw new IllegalStateException("请在宁宁后端检查信息整理模型，手机无需填写模型密钥");
    if (!settings.session().isEmpty()) {
      request("/v1/sessions/" + settings.session(), null);
      settings.connected(settings.session());
      return "已连接宁宁，使用后端现有整理模型";
    }
    JSONArray characters = request("/v1/characters", null).getJSONArray("items");
    if (characters.length() == 0) throw new IllegalStateException("宁宁后端尚未配置角色");
    String character = characters.getJSONObject(0).getString("character_id");
    for (int i = 0; i < characters.length(); i++)
      if (characters.getJSONObject(i).getString("character_id").equals("default"))
        character = "default";
    JSONObject session =
        request(
            "/v1/sessions",
            new JSONObject().put("character_id", character).put("participant_id", "local"));
    settings.connected(session.getString("session_id"));
    return "已连接宁宁，使用后端现有整理模型";
  }

  public JSONObject analyze(
      String requestId, JSONArray sources, JSONArray known, JSONArray context, String timezone)
      throws Exception {
    return analyze(requestId, sources, known, context, timezone, AnalysisSendGate.DIRECT);
  }

  public JSONObject analyze(
      String requestId,
      JSONArray sources,
      JSONArray known,
      JSONArray context,
      String timezone,
      AnalysisSendGate gate)
      throws Exception {
    if (!settings.ready()) throw new IllegalStateException("请先连接宁宁后端");
    AnalysisPayload payload = AnalysisPayload.prepare(sources, known, context, timezone);
    JSONObject body =
        new JSONObject(payload.input.toString())
            .put("schema_version", "1.1")
            .put("session_id", settings.session())
            .put("request_id", requestId);
    JSONObject result = request("/v1/personal-information/analyze", body, gate);
    if (!requestId.equals(result.optString("request_id"))
        || !result.optString("model_owner").equals("chatwaifu_runtime")
        || !result.optBoolean("requires_confirmation"))
      throw new IllegalStateException("宁宁返回的批次不匹配");
    JSONArray refs = result.getJSONArray("processed_refs");
    java.util.Set<String> received = new java.util.HashSet<>();
    for (int i = 0; i < refs.length(); i++) received.add(refs.getString(i));
    if (received.size() != refs.length() || !received.equals(payload.allowed.keySet()))
      throw new IllegalStateException("宁宁返回的处理进度不匹配");
    return payload.validate(result.getJSONArray("items"));
  }
}
