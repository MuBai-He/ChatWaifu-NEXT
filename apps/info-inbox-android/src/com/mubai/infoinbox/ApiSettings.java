package com.mubai.infoinbox;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** API key is encrypted using a key held in Android Keystore. It is never exported. */
public final class ApiSettings {
  private final String alias;
  private final SharedPreferences prefs;

  public ApiSettings(Context context) {
    this(context, "api", "info-inbox-api");
  }

  ApiSettings(Context context, String prefsName, String alias) {
    this.alias = alias;
    prefs = context.getSharedPreferences(prefsName, Context.MODE_PRIVATE);
  }

  public String base() {
    return prefs.getString("base", "https://api.openai.com/v1");
  }

  public String model() {
    return prefs.getString("model", "");
  }

  public boolean jsonMode() {
    return prefs.getBoolean("json_mode", true);
  }

  public boolean automatic() {
    return prefs.getBoolean("automatic", false);
  }

  public boolean hasKey() {
    return prefs.contains("encrypted_key");
  }

  public boolean ready() {
    return prefs.getBoolean("configured", false) && !model().isEmpty();
  }

  public String timezone() {
    return prefs.getString("timezone", "Asia/Shanghai");
  }

  public void timezone(String value) {
    if (!java.time.ZoneId.getAvailableZoneIds().contains(value))
      throw new IllegalArgumentException("需要 IANA 时区名称");
    java.time.ZoneId.of(value);
    prefs.edit().putString("timezone", value).apply();
  }

  private SecretKey secret() throws Exception {
    KeyStore store = KeyStore.getInstance("AndroidKeyStore");
    store.load(null);
    if (store.containsAlias(alias))
      return ((KeyStore.SecretKeyEntry) store.getEntry(alias, null)).getSecretKey();
    KeyGenerator generator =
        KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
    generator.init(
        new KeyGenParameterSpec.Builder(
                alias, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build());
    return generator.generateKey();
  }

  public void save(String base, String model, String key, boolean jsonMode) throws Exception {
    OpenAiClient.normalizeBase(base);
    if (model.length() > 200) throw new IllegalArgumentException("模型 ID 过长");
    SharedPreferences.Editor editor =
        prefs
            .edit()
            .putString("base", base.trim())
            .putString("model", model.trim())
            .putBoolean("json_mode", jsonMode)
            .putBoolean("configured", true);
    if (!key.trim().isEmpty()) {
      Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
      cipher.init(Cipher.ENCRYPT_MODE, secret());
      editor.putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP));
      editor.putString(
          "encrypted_key",
          Base64.encodeToString(
              cipher.doFinal(key.trim().getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP));
    }
    if (!editor.commit()) throw new Exception("接口配置保存失败");
  }

  public String key() throws Exception {
    if (!hasKey()) return "";
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(
        Cipher.DECRYPT_MODE,
        secret(),
        new GCMParameterSpec(128, Base64.decode(prefs.getString("iv", ""), Base64.NO_WRAP)));
    return new String(
        cipher.doFinal(Base64.decode(prefs.getString("encrypted_key", ""), Base64.NO_WRAP)),
        StandardCharsets.UTF_8);
  }

  public void automatic(boolean on) {
    prefs.edit().putBoolean("automatic", on).apply();
  }

  public OpenAiClient client() throws Exception {
    return new OpenAiClient(base(), model(), key(), jsonMode());
  }
}
