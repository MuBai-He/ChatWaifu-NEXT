package com.mubai.infoinbox;

import android.content.Context;
import android.database.Cursor;
import android.content.ContentValues;
import org.json.JSONArray;
import org.json.JSONObject;

/** Durable native-to-Runtime handoff. Source snapshots and receipts stay on this device. */
public final class RuntimeProcessing {
  private RuntimeProcessing() {}
  private static final Object ACTIVITY_LOCK=new Object();
  private static final Object ACTIVITY_WAIT=new Object();

  static void createTables(android.database.sqlite.SQLiteDatabase db) {
    RuntimeActions.createTables(db);
    boolean dueDate=false;
    try(Cursor columns=db.rawQuery("PRAGMA table_info(work_items)",null)){while(columns.moveToNext())if("due_date".equals(columns.getString(1)))dueDate=true;}
    if(!dueDate)db.execSQL("ALTER TABLE work_items ADD COLUMN due_date TEXT");
    db.execSQL("CREATE TABLE IF NOT EXISTS runtime_activity_ack(space TEXT NOT NULL,sequence INTEGER NOT NULL,state TEXT NOT NULL,PRIMARY KEY(space,sequence))");
    db.execSQL("CREATE TABLE IF NOT EXISTS runtime_processed(space TEXT NOT NULL,reference TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(space,reference,revision))");
  }

  private static void progress(KnowledgeStore store,RuntimeBackendClient client,BackendSettings backend) throws Exception {
    long cursor=Long.parseLong(store.meta("processing_cursor","0"));
    JSONObject page=client.request("/v1/personal-information/processing/progress?session_id="+backend.session()+"&after="+cursor,null);
    store.db.beginTransactionNonExclusive();
    try {
      JSONArray receipts=page.getJSONArray("items");
      for(int i=0;i<receipts.length();i++) {
        JSONObject value=receipts.getJSONObject(i);
        store.db.execSQL("INSERT OR IGNORE INTO runtime_processed VALUES(?,?,?)",new Object[]{store.space,value.getString("reference"),value.getInt("revision")});
      }
      try(Cursor c=store.db.rawQuery("SELECT ref,source_json FROM queued_sources q WHERE state='pending' AND EXISTS(SELECT 1 FROM source_snapshots s WHERE s.base_ref=q.base_ref AND s.revision=q.revision)",null)) {
        while(c.moveToNext()) {
          JSONArray refs=store.sourceRefs(new JSONObject(c.getString(1)));
          boolean done=refs.length()>0;
          for(int i=0;i<refs.length()&&done;i++)try(Cursor received=store.db.rawQuery("SELECT 1 FROM runtime_processed WHERE space=? AND reference=? AND revision=?",new String[]{store.space,refs.getJSONObject(i).getString("record_id"),refs.getJSONObject(i).getString("revision")})){done=received.moveToFirst();}
          if(done)store.db.execSQL("UPDATE queued_sources SET state='done' WHERE ref=? AND state='pending'",new Object[]{c.getString(0)});
        }
      }
      store.putMeta("processing_cursor",Long.toString(page.getLong("next_cursor")));
      store.db.setTransactionSuccessful();
    }finally{store.db.endTransaction();}
  }

  private static void retireReviews(KnowledgeStore store,String room) throws Exception {
    try(Cursor c=store.db.rawQuery("SELECT ref,item_json FROM ai_reviews WHERE state='pending'",null)) {
      while(c.moveToNext())if(KnowledgeWire.room(new JSONObject(c.getString(1)).getJSONObject("source")).equals(room))store.db.execSQL("UPDATE ai_reviews SET state='migrated' WHERE ref=?",new Object[]{c.getString(0)});
    }
  }

  public static boolean owns(Context context) {
    return !context.getSharedPreferences("runtime-processing", Context.MODE_PRIVATE)
        .getString(new BackendSettings(context).base(), "").isEmpty();
  }

  static void synchronize(Context context, KnowledgeStore store, RuntimeBackendClient client,
      BackendSettings backend) throws Exception {
    JSONObject status = client.request("/v1/personal-information/processing?session_id=" + backend.session(), null);
    JSONArray states = status.getJSONArray("items");
    JSONArray rooms = new JSONArray();
    try (Cursor c = store.db.rawQuery("SELECT room_id FROM knowledge_rooms WHERE space=? AND coalesce(remote_excluded,0)=0", new String[] {store.space})) {
      while (c.moveToNext()) {
        boolean enrolled = false;
        for (int i=0;i<states.length();i++) enrolled |= states.getJSONObject(i).getString("room_id").equals(c.getString(0));
        if (!enrolled) rooms.put(c.getString(0));
      }
    }
    if (rooms.length()>0) {
      JSONObject prepared = client.request("/v1/personal-information/processing", request(backend,store,rooms,"prepare",0));
      context.getSharedPreferences("runtime-processing", Context.MODE_PRIVATE).edit()
          .putString(backend.base(), "preparing").apply();
      context.getSystemService(android.app.job.JobScheduler.class).cancel(8802);
      states = client.request("/v1/personal-information/processing?session_id=" + backend.session(), null).getJSONArray("items");
    }
    for (int i=0;i<states.length();i++) {
      JSONObject state = states.getJSONObject(i);
      boolean runtime=state.getString("state").equals("runtime");
      boolean mine=state.getString("device_id").equals(store.device());
      if(runtime&&(mine||store.meta("joined:"+state.getString("room_id"),"").equals(Integer.toString(state.getInt("epoch"))))) {
        context.getSharedPreferences("runtime-processing", Context.MODE_PRIVATE).edit()
            .putString(backend.base(), "runtime").apply();
        store.putMeta("processing_epoch", Integer.toString(state.getInt("epoch")));
        retireReviews(store,state.getString("room_id"));
        continue;
      }
      if (!(runtime||state.getString("state").equals("preparing")&&mine)) continue;
      context.getSharedPreferences("runtime-processing",Context.MODE_PRIVATE).edit().putString(backend.base(),"preparing").apply();
      try (Cursor c=store.db.rawQuery("SELECT count(*) FROM analysis_runs WHERE state='running' AND lease_until>?",new String[]{Long.toString(System.currentTimeMillis())})) {
        if (c.moveToFirst() && c.getInt(0)>0) return;
      }
      store.db.execSQL("UPDATE queued_sources SET state='pending',run_id=NULL WHERE state='leased' AND run_id IN (SELECT id FROM analysis_runs WHERE state='running' AND lease_until<=?)",new Object[]{System.currentTimeMillis()});
      store.db.execSQL("UPDATE analysis_runs SET state='expired' WHERE state='running' AND lease_until<=?",new Object[]{System.currentTimeMillis()});
      JSONArray one = new JSONArray().put(state.getString("room_id"));
      String after="";
      boolean complete=false;
      while (!complete) {
        JSONObject snapshot=request(backend,store,one,"prepare",state.getInt("epoch"));
        JSONArray done=new JSONArray();
        int read=0;
        try (Cursor c=store.db.rawQuery("SELECT ref,source_json FROM queued_sources WHERE state='done' AND ref>? ORDER BY ref LIMIT 100",new String[]{after})) {
          while(c.moveToNext()) {
            after=c.getString(0);read++;
            JSONObject source=new JSONObject(c.getString(1));
            if (!KnowledgeWire.room(source).equals(state.getString("room_id"))) continue;
            JSONArray refs=store.sourceRefs(source);
            if(refs.length()==0) return; // Wait for the original versions to be acknowledged.
            for(int r=0;r<refs.length();r++) {
              JSONObject ref=refs.getJSONObject(r);
              done.put(new JSONObject().put("kind","knowledge").put("reference",ref.getString("record_id")).put("revision",ref.getInt("revision")));
            }
          }
        }
        complete=read<100;
        snapshot.put("completed_sources",done);
        client.request("/v1/personal-information/processing",snapshot);
      }
      JSONArray pending=new JSONArray();
      try(Cursor c=store.db.rawQuery("SELECT ref,item_json FROM ai_reviews WHERE state='pending' ORDER BY ref",null)) {
        while(c.moveToNext()) {
          JSONObject proposal=new JSONObject(c.getString(1)), source=proposal.getJSONObject("source");
          if(!KnowledgeWire.room(source).equals(state.getString("room_id")))continue;
          JSONArray refs=store.sourceRefs(source), evidence=new JSONArray();
          if(refs.length()==0)return;
          for(int r=0;r<refs.length();r++) evidence.put(new JSONObject().put("kind","knowledge").put("reference",refs.getJSONObject(r).getString("record_id")).put("revision",refs.getJSONObject(r).getInt("revision")));
          String kind=proposal.optString("kind","reference");
          if(!kind.matches("todo|event|memory|decision|reference"))kind="reference";
          JSONObject mapping=new JSONObject();String targetId=proposal.optString("target_id","");
          if(!targetId.isEmpty()) {
            int localRevision=0;try(Cursor local=store.db.rawQuery("SELECT revision FROM work_items WHERE id=?",new String[]{targetId})){if(local.moveToFirst())localRevision=local.getInt(0);}
            try(Cursor shadow=store.db.rawQuery("SELECT id,revision,payload FROM knowledge_shadow WHERE space=? AND local_id=? AND revision>0",new String[]{store.space,targetId})){
              if(localRevision>0&&shadow.moveToFirst())mapping.put("record_id",shadow.getString(0)).put("record_revision",shadow.getInt(1)).put("native_revision",localRevision).put("expected_native_revision",proposal.optInt("expected_revision",0));
            }
          }
          pending.put(new JSONObject().put("migration_target",mapping).put("work_item_id","legacy-review:"+AnalysisContract.hash(c.getString(0)))
              .put("room_id",state.getString("room_id")).put("kind",kind).put("title",proposal.optString("title","待核对信息"))
              .put("details",proposal.optString("details",proposal.optString("reason")))
              .put("state","proposed").put("acceptance","pending").put("evidence",evidence)
              .put("involvement",proposal.optString("involvement","unclear"))
              .put("operation",proposal.optString("operation","create")).put("legacy_review",true)
              .put("due_at",proposal.opt("due_at")).put("starts_at",proposal.opt("starts_at")).put("ends_at",proposal.opt("ends_at"))
              .put("updated_at",java.time.Instant.now().toString()));
          if(pending.length()==100) {
            client.request("/v1/personal-information/processing",request(backend,store,one,"prepare",state.getInt("epoch")).put("pending_reviews",pending));
            pending=new JSONArray();
          }
        }
      }
      if(pending.length()>0)client.request("/v1/personal-information/processing",request(backend,store,one,"prepare",state.getInt("epoch")).put("pending_reviews",pending));
      JSONObject activated=client.request("/v1/personal-information/processing",request(backend,store,one,"activate",state.getInt("epoch")).put("drained",true));
      store.putMeta("processing_epoch",Integer.toString(activated.getInt("epoch")));
      store.putMeta("joined:"+state.getString("room_id"),Integer.toString(activated.getInt("epoch")));
      retireReviews(store,state.getString("room_id"));
      context.getSharedPreferences("runtime-processing",Context.MODE_PRIVATE).edit().putString(backend.base(),"runtime").apply();
    }
    RuntimeActions.flush(store,client,backend);
    progress(store,client,backend);
    notifyChanges(context,store,client,backend);
    RuntimeMessageService.ensureStarted(context);
  }

  static void acknowledge(KnowledgeStore store,RuntimeBackendClient client,BackendSettings backend,long sequence,String state) throws Exception {
    store.db.execSQL("INSERT INTO runtime_activity_ack VALUES(?,?,?) ON CONFLICT(space,sequence) DO UPDATE SET state=excluded.state",new Object[]{store.space,sequence,state});
    flushAcknowledgements(store,client,backend);
  }

  private static void flushAcknowledgements(KnowledgeStore store,RuntimeBackendClient client,BackendSettings backend) throws Exception {
    try(Cursor c=store.db.rawQuery("SELECT sequence,state FROM runtime_activity_ack WHERE space=? ORDER BY sequence LIMIT 100",new String[]{store.space})) {
      while(c.moveToNext()) {
        client.request("/v1/agent/activity/"+c.getLong(0)+"/ack",new JSONObject().put("session_id",backend.session()).put("device_id",store.device()).put("state",c.getString(1)));
        store.db.execSQL("DELETE FROM runtime_activity_ack WHERE space=? AND sequence=? AND state=?",new Object[]{store.space,c.getLong(0),c.getString(1)});
      }
    }
  }

  static boolean receiveActivity(Context context) throws Exception {
    return receiveActivity(context,true);
  }

  static void wakeActivityPoll() {
    synchronized(ACTIVITY_WAIT) { ACTIVITY_WAIT.notifyAll(); }
  }

  static boolean receiveActivity(Context context,boolean wait) throws Exception {
    BackendSettings backend=new BackendSettings(context);
    try(MemoryStore memory=new MemoryStore(context)) {
      KnowledgeStore store=new KnowledgeStore(memory,backend.base());
      synchronized(ACTIVITY_LOCK) {
        String before=store.meta("activity_cursor","");
        notifyChanges(context,store,backend.client(),backend,wait);
        return !before.equals(store.meta("activity_cursor",""));
      }
    }
  }

  static void notifyChanges(Context context,KnowledgeStore store,RuntimeBackendClient client,BackendSettings backend) throws Exception {
    synchronized(ACTIVITY_LOCK) {notifyChanges(context,store,client,backend,false);}
  }

  private static void notifyChanges(Context context,KnowledgeStore store,RuntimeBackendClient client,BackendSettings backend,boolean wait) throws Exception {
    flushAcknowledgements(store,client,backend);
    String previous=store.meta("activity_cursor","");
    JSONObject page=client.request("/v1/agent/activity?session_id="+backend.session()+"&after="+(previous.isEmpty()?0:previous)+(wait&&!previous.isEmpty()?"&wait_seconds=25":""),null);
    String epoch=page.optString("context_epoch","");
    if(!epoch.equals(store.meta("activity_context_epoch",""))) {
      store.putMeta("activity_pending_summaries","[]");
      store.putMeta("harness_snapshot","");
      store.putMeta("activity_context_epoch",epoch);
      android.app.NotificationManager notices=context.getSystemService(android.app.NotificationManager.class);
      for(android.service.notification.StatusBarNotification shown:notices.getActiveNotifications())if("ningning-action".equals(shown.getTag()))notices.cancel(shown.getTag(),shown.getId());
    }
    if(page.optBoolean("quiet")&&!previous.isEmpty()) { if(wait) { synchronized(ACTIVITY_WAIT){ACTIVITY_WAIT.wait(25000);} } return; }
    JSONArray values=page.getJSONArray("items"),ordinary=new JSONArray();
    JSONArray pending=new JSONArray(store.meta("activity_pending_summaries","[]"));
    for(int i=0;i<pending.length();i++)ordinary.put(pending.getJSONObject(i));
    for(int i=0;i<values.length();i++) {
      JSONObject entry=values.getJSONObject(i);String kind=entry.getString("kind");
      if(previous.isEmpty()||!(kind.startsWith("action.")||kind.equals("waiting")))continue;
      long created=java.time.Instant.parse(entry.getString("created_at")).toEpochMilli();
      if(System.currentTimeMillis()-created>86400000)continue;
      boolean important=kind.equals("action.event")||kind.equals("waiting")||!entry.getString("state").equals("verified");
      if(important)postNotice(context,store,client,backend,entry,entry.getString("summary"));
      else if(ordinary.length()<100)ordinary.put(entry);
    }
    long last=Long.parseLong(store.meta("activity_summary_at","0"));
    if(ordinary.length()>0&&System.currentTimeMillis()-last>=300000) {
      JSONObject entry=ordinary.getJSONObject(ordinary.length()-1);
      String state=postNotice(context,store,client,backend,entry,"已完成 "+ordinary.length()+" 项处理，可打开查看详情。");
      for(int i=0;i<ordinary.length()-1;i++)acknowledge(store,client,backend,ordinary.getJSONObject(i).getLong("sequence"),state);
      ordinary=new JSONArray();store.putMeta("activity_summary_at",Long.toString(System.currentTimeMillis()));
    }
    store.putMeta("activity_pending_summaries",ordinary.toString());
    store.putMeta("activity_cursor",Long.toString(page.getLong("next_cursor")));
  }

  private static String postNotice(Context context,KnowledgeStore store,RuntimeBackendClient client,BackendSettings backend,JSONObject entry,String summary) throws Exception {
    long sequence=entry.getLong("sequence");String key="activity_notice:"+sequence;
    String state=store.meta(key,"");
    android.app.NotificationManager manager=context.getSystemService(android.app.NotificationManager.class);
    if(state.equals("posting")) {
      state="unknown";
      for(android.service.notification.StatusBarNotification visible:manager.getActiveNotifications())if("ningning-action".equals(visible.getTag())&&visible.getId()==(int)(sequence%Integer.MAX_VALUE))state="presented";
    }
    if(state.isEmpty()) {
      state="blocked";
      if(ReminderScheduler.notificationsAllowed(context)) {
        android.app.NotificationChannel channel=new android.app.NotificationChannel("ningning-actions","宁宁的重要变化",android.app.NotificationManager.IMPORTANCE_DEFAULT);
        channel.setLockscreenVisibility(android.app.Notification.VISIBILITY_PRIVATE);manager.createNotificationChannel(channel);
        android.content.Intent open=new android.content.Intent(context,HarnessActivity.class).putExtra("activity_sequence",sequence);
        android.app.PendingIntent pending=android.app.PendingIntent.getActivity(context,(int)(sequence%Integer.MAX_VALUE),open,android.app.PendingIntent.FLAG_UPDATE_CURRENT|android.app.PendingIntent.FLAG_IMMUTABLE);
        store.putMeta(key,"posting");
        manager.notify("ningning-action",(int)(sequence%Integer.MAX_VALUE),new android.app.Notification.Builder(context,"ningning-actions").setSmallIcon(com.mubai.infoinbox.R.drawable.app_icon).setContentTitle("宁宁的处理进展").setContentText(summary).setVisibility(android.app.Notification.VISIBILITY_PRIVATE).setContentIntent(pending).setAutoCancel(true).setOnlyAlertOnce(true).build());
        state="accepted";
        for(android.service.notification.StatusBarNotification visible:manager.getActiveNotifications())if("ningning-action".equals(visible.getTag())&&visible.getId()==(int)(sequence%Integer.MAX_VALUE))state="presented";
      }
    }
    store.putMeta(key,state);acknowledge(store,client,backend,sequence,state);return state;
  }

  private static JSONObject request(BackendSettings backend,KnowledgeStore store,JSONArray rooms,String action,int epoch) throws Exception {
    JSONObject watermarks=new JSONObject();
    for(int i=0;i<rooms.length();i++)watermarks.put(rooms.getString(i),System.currentTimeMillis());
    return new JSONObject().put("session_id",backend.session()).put("device_id",store.device())
        .put("room_ids",rooms).put("action",action).put("expected_epoch",epoch).put("watermarks",watermarks);
  }
}
