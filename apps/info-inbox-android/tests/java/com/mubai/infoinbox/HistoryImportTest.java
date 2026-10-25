package com.mubai.infoinbox;

import static org.junit.Assert.*;

import android.app.Application;
import android.content.Context;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 33, application = Application.class)
public class HistoryImportTest {
  @Test
  public void pauseRetainsIntentAndLateCompletionCannotReplaceIt() {
    Context context = RuntimeEnvironment.getApplication();
    HistoryImport.request(context);
    assertTrue(HistoryImport.requested(context));
    HistoryImport.pause(context, "暂停，保留进度");
    HistoryImport.complete(context);
    assertFalse(HistoryImport.requested(context));
    assertEquals("暂停，保留进度", HistoryImport.status(context));
  }

  @Test
  public void partialOrFailedQueuesNeverClaimComplete() throws Exception {
    JSONObject stats = new JSONObject().put("pending", 0).put("leased", 0).put("failed", 0);
    assertTrue(HistoryImport.drained(0, stats, 0, 0));
    assertFalse(HistoryImport.drained(1, stats, 0, 0));
    assertFalse(HistoryImport.drained(0, stats, 1, 0));
    assertFalse(HistoryImport.drained(0, stats, 0, 1));
    stats.put("failed", 1);
    assertFalse(HistoryImport.drained(0, stats, 0, 0));
    stats.put("failed", 0).put("leased", 1);
    assertFalse(HistoryImport.drained(0, stats, 0, 0));
  }

  @Test
  public void platformTimeoutPausesAndPreservesResumePath() {
    Context context = RuntimeEnvironment.getApplication();
    HistoryImport.request(context);
    org.robolectric.android.controller.ServiceController<HistoryImportService> controller =
        Robolectric.buildService(HistoryImportService.class).create();
    controller.get().onTimeout(1, 1);
    assertTrue(HistoryImport.requested(context));
    assertTrue(HistoryImport.status(context).contains("进度已保留"));
    controller.destroy();
    HistoryImport.request(context);
    assertTrue(HistoryImport.requested(context));
  }
}
