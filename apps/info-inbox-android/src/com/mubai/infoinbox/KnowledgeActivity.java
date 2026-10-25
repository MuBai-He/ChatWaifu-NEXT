package com.mubai.infoinbox;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.ByteArrayInputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/** Same-origin map only. No JavascriptInterface, filesystem access or Root bridge. */
public final class KnowledgeActivity extends Activity {
  private WebView web;
  private TextView status;
  private final ExecutorService work = Executors.newSingleThreadExecutor();
  private String base;

  @Override
  @SuppressLint("SetJavaScriptEnabled")
  public void onCreate(Bundle state) {
    super.onCreate(state);
    LinearLayout page = new LinearLayout(this);
    page.setOrientation(LinearLayout.VERTICAL);
    page.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              insets.getSystemWindowInsetLeft(),
              insets.getSystemWindowInsetTop(),
              insets.getSystemWindowInsetRight(),
              insets.getSystemWindowInsetBottom());
          return insets;
        });
    LinearLayout actions = new LinearLayout(this);
    Button back = new Button(this);
    back.setText("返回收件箱");
    NativeUi.action(back);
    back.setOnClickListener(v -> finish());
    actions.addView(back, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
    Button refresh = new Button(this);
    refresh.setText("同步并刷新");
    NativeUi.action(refresh);
    refresh.setOnClickListener(v -> load());
    actions.addView(
        refresh, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
    page.addView(actions);
    status = new TextView(this);
    status.setPadding(16, 8, 16, 8);
    page.addView(status);
    web = new WebView(this);
    WebSettings settings = web.getSettings();
    settings.setJavaScriptEnabled(true);
    settings.setDomStorageEnabled(true);
    settings.setAllowFileAccess(false);
    settings.setAllowContentAccess(false);
    settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    settings.setSupportMultipleWindows(false);
    settings.setJavaScriptCanOpenWindowsAutomatically(false);
    CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
    web.setWebViewClient(
        new WebViewClient() {
          @Override
          public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            if ("infoinbox".equals(url.getScheme())
                && "source".equals(url.getHost())
                && request.isForMainFrame()) {
              showSource(url);
              return true;
            }
            return !allowed(url) || !"/knowledge".equals(url.getPath());
          }

          @Override
          public WebResourceResponse shouldInterceptRequest(
              WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            String path = url.getPath();
            if (allowed(url)
                && path != null
                && (path.equals("/knowledge")
                    || path.startsWith("/knowledge-assets/")
                    || path.startsWith("/v1/knowledge/")
                        && !path.equals("/v1/knowledge/viewer-session")
                    || path.equals("/favicon.ico"))) return null;
            return new WebResourceResponse(
                "text/plain",
                "UTF-8",
                403,
                "Blocked",
                java.util.Collections.emptyMap(),
                new ByteArrayInputStream(new byte[0]));
          }
        });
    page.addView(web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
    setContentView(page);
    load();
  }

  private boolean allowed(Uri url) {
    if (base == null) return false;
    Uri origin = Uri.parse(base);
    return "https".equals(url.getScheme())
        && origin.getHost() != null
        && origin.getHost().equals(url.getHost())
        && origin.getPort() == url.getPort()
        && url.getUserInfo() == null;
  }

  private void load() {
    status.setText("正在同步并连接知识地图…");
    work.execute(
        () -> {
          try {
            BackendSettings backend = new BackendSettings(this);
            if (!backend.ready()) throw new IllegalStateException("请先在设置中连接宁宁后端");
            base = BackendSettings.normalize(backend.base());
            if (!base.startsWith("https://")) throw new IllegalStateException("知识地图需要 HTTPS 后端连接");
            String syncError = "";
            try {
              KnowledgeSync.run(this);
            } catch (Exception unavailable) {
              syncError = "同步尚未完成，显示服务器已有资料";
            }
            JSONObject viewer;
            try (MemoryStore memory = new MemoryStore(this)) {
              KnowledgeStore store = new KnowledgeStore(memory, backend.base());
              viewer =
                  backend
                      .client()
                      .request(
                          "/v1/knowledge/viewer-session",
                          new JSONObject()
                              .put("session_id", backend.session())
                              .put("device_id", store.device()));
            }
            final String message = syncError;
            runOnUiThread(
                () -> {
                  if (isFinishing() || isDestroyed()) return;
                  status.setText(message);
                  CookieManager.getInstance()
                      .setCookie(
                          base + "/v1/knowledge",
                          viewer.optString("cookie"),
                          accepted -> {
                            if (isFinishing() || isDestroyed()) return;
                            if (!accepted) {
                              status.setText("临时连接凭证未能保存，请重试");
                              return;
                            }
                            web.loadUrl(
                                base
                                    + "/knowledge?embedded=1&session_id="
                                    + Uri.encode(backend.session()));
                          });
                });
          } catch (Exception error) {
            runOnUiThread(
                () -> {
                  if (!isDestroyed())
                    status.setText(error.getMessage() == null ? "连接未完成，请稍后重试" : error.getMessage());
                });
          }
        });
  }

  private void showSource(Uri uri) {
    String id = uri.getLastPathSegment();
    if (id == null || !id.matches("wechat:[0-9a-f]{64}:part:[0-9]+")) return;
    work.execute(
        () -> {
          try (MemoryStore memory = new MemoryStore(this)) {
            JSONObject source =
                new KnowledgeStore(memory, new BackendSettings(this).base()).localSource(id);
            if (source == null) return;
            runOnUiThread(
                () -> {
                  if (!isDestroyed())
                    new AlertDialog.Builder(this)
                        .setTitle(MessagePresentation.conversationTitle(source))
                        .setMessage(
                            "本机最新原文；网页引用的历史版本请在网页核对。\n\n" + MessagePresentation.body(source))
                        .setPositiveButton("关闭", null)
                        .show();
                });
          } catch (Exception unavailable) {
            runOnUiThread(
                () -> {
                  if (!isDestroyed()) status.setText("本机暂时没有这条资料，请查看网页中的版本快照");
                });
          }
        });
  }

  @Override
  public void onBackPressed() {
    finish();
  }

  @Override
  public void onDestroy() {
    work.shutdownNow();
    if (web != null) {
      web.stopLoading();
      web.destroy();
    }
    super.onDestroy();
  }
}
