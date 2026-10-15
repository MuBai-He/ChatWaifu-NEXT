package com.mubai.infoinbox;

import android.app.Instrumentation;
import android.content.Context;
import android.os.Bundle;
import org.json.JSONObject;

/** Debug-only owner acceptance. No original text or credentials are printed. */
public final class HistoryImportInstrumentation extends Instrumentation {
  private String mode;

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    mode = args == null ? "probe" : args.getString("mode", "probe");
    start();
  }

  @Override
  public void onStart() {
    Bundle out = new Bundle();
    boolean passed = false;
    Context context = getTargetContext();
    String phase = "probe";
    try (MemoryStore memory = new MemoryStore(context)) {
      if (mode.equals("cycle")) {
        phase = "prepare";
        new RootGateway(context).prepareHistoryCollection();
        phase = "intake";
        AnalysisEngine.intake(context, true);
        phase = "knowledge";
        KnowledgeSync.run(context, true);
        phase = "analysis";
        AnalysisEngine.run(context, true);
      } else if (mode.equals("start")) {
        if (!new BackendSettings(context).ready())
          throw new IllegalStateException("backend_not_ready");
        new OrganizerStore(memory).retryFailed();
        HistoryImport.request(context);
        context
            .getSharedPreferences("knowledge", Context.MODE_PRIVATE)
            .edit()
            .putBoolean("enabled", true)
            .commit();
        AnalysisEngine.intake(context, true);
        AnalysisJob.schedule(context, new ApiSettings(context).automatic());
        AnalysisJob.enqueueIntake(context, 0);
        KnowledgeJob.enqueueNow(context);
      } else if (mode.equals("pause")) HistoryImportService.pause(context);
      JSONObject stats = new OrganizerStore(memory).queueStats();
      out.putBoolean("full_history_requested", HistoryImport.requested(context));
      out.putBoolean("wifi", KnowledgeSync.wifi(context));
      out.putInt("pending", stats.optInt("pending"));
      out.putInt("done", stats.optInt("done"));
      out.putInt("excluded", new ConversationPolicy(memory).count());
      passed = true;
    } catch (Throwable failure) {
      out.putString("error_type", failure.getClass().getSimpleName());
      if (failure instanceof RootGateway.RootFailure) {
        out.putString("root_error_code", ((RootGateway.RootFailure) failure).code);
        out.putInt("root_exit_code", ((RootGateway.RootFailure) failure).exitCode);
      }
      String message = failure.getMessage();
      if (message != null) {
        if (message.contains("Root 请求超时")) out.putString("error_code", "root_timeout");
        else if (message.contains("操作失败")) out.putString("error_code", "root_command_failed");
        else if (message.contains("后端拒绝连接")) out.putString("error_code", "backend_auth");
        else if (message.contains("服务暂不可用")) out.putString("error_code", "backend_unavailable");
        else if (message.contains("后端返回 HTTP")) {
          java.util.regex.Matcher code =
              java.util.regex.Pattern.compile("HTTP ([0-9]{3})").matcher(message);
          if (code.find()) out.putString("error_code", "http_" + code.group(1));
        }
      }
    }
    out.putString("phase", phase);
    out.putBoolean("passed", passed);
    finish(passed ? -1 : 0, out);
  }
}
