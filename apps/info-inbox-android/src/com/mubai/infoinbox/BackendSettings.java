package com.mubai.infoinbox;

import android.content.Context;
import android.content.SharedPreferences;
import java.net.URI;
import java.util.UUID;

/** Runtime connection credentials are separate from optional standalone model credentials. */
public final class BackendSettings {
  private final ApiSettings secret;
  private final SharedPreferences prefs;

  public BackendSettings(Context context) {
    this(context, "backend", "info-inbox-backend");
  }

  BackendSettings(Context context, String name, String alias) {
    secret = new ApiSettings(context, name, alias);
    prefs = context.getSharedPreferences(name, Context.MODE_PRIVATE);
  }

  public String base() {
    return prefs.getString("base", "");
  }

  public boolean hasCredential() {
    return secret.hasKey();
  }

  public String credential() throws Exception {
    return secret.key();
  }

  public String session() {
    return prefs.getString("owner_session", "");
  }

  public boolean ready() {
    return secret.ready() && prefs.getBoolean("connected", false) && !session().isEmpty();
  }

  public void save(String raw, String token) throws Exception {
    String base = normalize(raw);
    if (token.trim().isEmpty() && !secret.hasKey())
      throw new IllegalArgumentException("请填写宁宁后端的连接凭据");
    if (!token.trim().isEmpty() && token.trim().length() < 32)
      throw new IllegalArgumentException("连接凭据不完整");
    if (secret.hasKey()
        && !base().isEmpty()
        && !URI.create(base()).equals(URI.create(base))
        && token.trim().isEmpty()) {
      throw new IllegalArgumentException("更换服务器时请重新填写该服务器的连接凭据");
    }
    boolean changed = !base.equals(base()) || !token.trim().isEmpty();
    secret.save(base, "runtime-connection", token, false);
    if (changed) prefs.edit().remove("owner_session").putBoolean("connected", false).commit();
  }

  public void connected(String session) {
    UUID.fromString(session);
    if (!prefs.edit().putString("owner_session", session).putBoolean("connected", true).commit())
      throw new IllegalStateException("连接状态保存失败");
  }

  public static String normalize(String raw) throws Exception {
    String normalized = OpenAiClient.normalizeBase(raw);
    URI uri = URI.create(normalized);
    if (!uri.getPath().equals("/v1")) throw new IllegalArgumentException("请填写宁宁服务器根地址");
    return normalized.substring(0, normalized.length() - 3);
  }

  public RuntimeBackendClient client() throws Exception {
    return new RuntimeBackendClient(this);
  }
}
