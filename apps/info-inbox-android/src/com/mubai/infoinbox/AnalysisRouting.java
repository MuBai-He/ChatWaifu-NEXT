package com.mubai.infoinbox;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;

public final class AnalysisRouting {
  private AnalysisRouting() {}

  public static boolean standalone(Context context) {
    return context
        .getSharedPreferences("analysis-route", Context.MODE_PRIVATE)
        .getBoolean("standalone", false);
  }

  public static void standalone(Context context, boolean value) {
    context
        .getSharedPreferences("analysis-route", Context.MODE_PRIVATE)
        .edit()
        .putBoolean("standalone", value)
        .apply();
  }

  public static boolean ready(Context context) {
    return standalone(context)
        ? new ApiSettings(context).ready()
        : new BackendSettings(context).ready();
  }

  public static String status(Context context) {
    return standalone(context) ? "独立接口模式" : "宁宁后端模式";
  }

  public static JSONObject analyze(
      Context context, String requestId, JSONArray sources, JSONArray known, JSONArray prior)
      throws Exception {
    return analyze(context, requestId, sources, known, prior, AnalysisSendGate.DIRECT);
  }

  public static JSONObject analyze(
      Context context,
      String requestId,
      JSONArray sources,
      JSONArray known,
      JSONArray prior,
      AnalysisSendGate gate)
      throws Exception {
    String timezone = new ApiSettings(context).timezone();
    if (standalone(context))
      return new ApiSettings(context).client().analyze(sources, known, prior, timezone, gate);
    return new BackendSettings(context)
        .client()
        .analyze(requestId, sources, known, prior, timezone, gate);
  }
}
