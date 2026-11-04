package com.mubai.infoinbox;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONArray;
import org.json.JSONObject;

/** Native view of the same owner intents, review and activity shown by Web/desktop. */
public final class HarnessActivity extends Activity {
  private final ExecutorService worker = Executors.newSingleThreadExecutor();
  private final ExecutorService submissions = Executors.newSingleThreadExecutor();
  private final java.util.Set<String> submitting = new java.util.HashSet<>();
  private final java.util.Set<String> queuedPaths = new java.util.HashSet<>();
  private JSONObject displayedSnapshot;
  private boolean displayedOffline;
  private String displayedIdentity = "";
  private LinearLayout content;
  private int generation;
  private boolean refreshing, refreshAgain;
  private int workOffset;
  private long activityBefore;
  private final java.util.ArrayList<Long> activityPages=new java.util.ArrayList<>();
  private String connection="";

  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    content = new LinearLayout(this);
    content.setOrientation(LinearLayout.VERTICAL);
    int pad = NativeUi.dp(this, 16);
    content.setPadding(pad, pad, pad, pad);
    ScrollView scroll = new ScrollView(this);
    scroll.addView(content);
    setContentView(scroll);
    refresh();
  }

  @Override public void onResume() {
    super.onResume();
    if(displayedSnapshot!=null)refresh();
  }

  private void label(String value, int size) {
    TextView text = new TextView(this);
    text.setText(value);
    text.setTextSize(size);
    text.setPadding(0, NativeUi.dp(this, 8), 0, NativeUi.dp(this, 8));
    content.addView(text);
  }

  private String currentIdentity() {
    BackendSettings settings=new BackendSettings(this);
    return settings.base()+"\n"+settings.session();
  }

  private boolean current(int ticket,String identity) {
    return !isFinishing()&&!isDestroyed()&&ticket==generation&&identity.equals(currentIdentity());
  }

  private void refresh() {
    final int ticket = ++generation;
    final String requestedIdentity=currentIdentity();
    if(!requestedIdentity.equals(connection)) {
      connection=requestedIdentity;workOffset=0;activityBefore=0;activityPages.clear();
    }
    final int requestedOffset=workOffset;
    final long requestedBefore=activityBefore;
    if(displayedSnapshot==null||!displayedIdentity.equals(currentIdentity())) {
      displayedSnapshot=null;
      content.removeAllViews();
      label("宁宁持续协助", 24);
      label("正在读取委托、核对与行动记录…", 14);
    }
    if(refreshing){refreshAgain=true;return;}
    refreshing=true;
    worker.execute(() -> {
      try (MemoryStore memory = new MemoryStore(this)) {
        BackendSettings backend = new BackendSettings(this);
        String identity=backend.base()+"\n"+backend.session();
        if(!identity.equals(requestedIdentity))return;
        KnowledgeStore store = new KnowledgeStore(memory, backend.base());
        RuntimeBackendClient client = backend.client();
        JSONObject snapshot;
        boolean offline = false;
        String cached=store.meta("harness_snapshot","");
        if(!cached.isEmpty()&&!new JSONObject(cached).optString("_connection").equals(identity))cached="";
        if(!cached.isEmpty()) {
          JSONObject ready=new JSONObject(cached);
          JSONArray drafts=RuntimeActions.list(store);
          if(ready.optInt("work_offset")==requestedOffset&&ready.optLong("activity_before")==requestedBefore)
            runOnUiThread(()->{
              if(!current(ticket,identity))return;
              try{render(ready,drafts,true);}catch(Exception invalid){/* Fresh state follows. */}
            });
        }
        try {
          RuntimeActions.flush(store, client, backend);
          long opened = getIntent().getLongExtra("activity_sequence", 0);
          if (opened > 0) {
            RuntimeProcessing.acknowledge(store, client, backend, opened, "opened");
            getIntent().removeExtra("activity_sequence");
          }
          String session = backend.session();
          JSONObject workPage=client.request("/v1/personal-assistant/work-items?session_id="+session+"&limit=30&offset="+requestedOffset,null);
          JSONObject activityPage=client.request("/v1/agent/activity?session_id="+session+(requestedBefore>0?"&before="+requestedBefore:""),null);
          snapshot = new JSONObject()
              .put("review", client.request("/v1/agent/review?session_id=" + session + "&include_failures=true", null).getJSONArray("items"))
              .put("intents", client.request("/v1/agent/intents?session_id=" + session, null).getJSONArray("items"))
              .put("work",workPage.getJSONArray("items")).put("work_offset",requestedOffset)
              .put("work_next",workPage.opt("next_offset"))
              .put("activity",activityPage.getJSONArray("items")).put("activity_before",requestedBefore)
              .put("activity_next",activityPage.opt("next_before"));
          if(!identity.equals(currentIdentity()))return;
          snapshot.put("_connection",identity);
          store.putMeta("harness_snapshot", snapshot.toString());
        } catch (Exception unavailable) {
          if (cached.isEmpty()) throw unavailable;
          snapshot = new JSONObject(cached);
          offline = true;
        }
        JSONObject view = snapshot;
        boolean disconnected = offline;
        JSONArray queued = RuntimeActions.list(store);
        runOnUiThread(() -> {
          if (!current(ticket,identity)) return;
          try { render(view, queued, disconnected); }
          catch (Exception invalid) { label("记录格式已变化，请刷新后重试。", 14); }
        });
      } catch (Exception unavailable) {
        runOnUiThread(() -> {
          if (!current(ticket,requestedIdentity)) return;
          content.removeAllViews();
          label("暂时无法连接宁宁，本机资料和提醒仍可使用。", 16);
          content.addView(NativeUi.button(this, "重试", this::refresh, true));
        });
      } finally {
        runOnUiThread(()->{
          refreshing=false;
          if(refreshAgain&&!isFinishing()&&!isDestroyed()) {
            refreshAgain=false;
            refresh();
          }
        });
      }
    });
  }

  private void decideReview(JSONObject item,String decision){
    try {
      BackendSettings backend=new BackendSettings(this);
      submit("/v1/agent/review/"+item.getString("kind")+"/"+item.getString("reference"),
          new JSONObject().put("decision",decision).put("expected_revision",item.getInt("revision"))
              .put("session_id",backend.session()),item.getString("title"));
    }catch(Exception invalid){Toast.makeText(this,"决定尚未提交，请重试。",Toast.LENGTH_LONG).show();}
  }

  private void submit(String path,JSONObject body,String label) {
    if(queuedPaths.contains(path)||!submitting.add(path))return;
    final String identity=currentIdentity();
    final String base=new BackendSettings(this).base();
    submissions.execute(()->{
      try(MemoryStore memory=new MemoryStore(getApplicationContext())) {
        if(!identity.equals(currentIdentity()))throw new IllegalStateException("连接已变化");
        KnowledgeStore store=new KnowledgeStore(memory,base);
        RuntimeActions.enqueue(store,path,body,label);
        JSONArray queued=RuntimeActions.list(store);
        runOnUiThread(()->{
          submitting.remove(path);
          if(isFinishing()||isDestroyed()||!identity.equals(currentIdentity()))return;
          Toast.makeText(this,"操作已保存在本机，等待宁宁接收并继续处理。",Toast.LENGTH_LONG).show();
          try {
            if(displayedSnapshot!=null&&displayedIdentity.equals(identity)
                &&displayedSnapshot.optInt("work_offset",0)==workOffset
                &&displayedSnapshot.optLong("activity_before",0)==activityBefore)
              render(displayedSnapshot,queued,displayedOffline);
          }catch(Exception invalid){/* The durable decision remains in the outbox. */}
          refresh();
        });
      }catch(Exception invalid){runOnUiThread(()->{
        submitting.remove(path);
        if(!isFinishing()&&!isDestroyed())
          Toast.makeText(this,"本机提交未完成，请重试。",Toast.LENGTH_LONG).show();
      });}
    });
  }

  private void render(JSONObject snapshot, JSONArray queued, boolean offline) throws Exception {
    displayedSnapshot=snapshot;
    displayedOffline=offline;
    displayedIdentity=currentIdentity();
    queuedPaths.clear();
    for(int i=0;i<queued.length();i++)queuedPaths.add(queued.getJSONObject(i).getString("path"));
    content.removeAllViews();
    label("宁宁持续协助", 24);
    content.addView(NativeUi.button(this, "刷新状态", this::refresh, false));
    if (offline) label("当前显示本机缓存，状态在后台更新。暂停或撤销需送达宁宁后才生效。", 14);
    for (int i = 0; i < queued.length(); i++) {
      JSONObject action = queued.getJSONObject(i);
      label((action.getString("state").equals("conflict") ? "需重新核对：" : "已提交本机，等待宁宁接收：") + action.getString("label"), 14);
      content.addView(NativeUi.button(this, "查看本机修改", () -> showDraft(action), false));
    }
    label("持续委托", 20);
    JSONArray intents = snapshot.getJSONArray("intents");
    if (intents.length() == 0) label("还没有持续委托，可直接交办给宁宁，或在能力与任务设置中添加。", 14);
    for (int i = 0; i < intents.length(); i++) {
      JSONObject item = intents.getJSONObject(i);
      String state = item.getString("state");
      label(item.getString("goal"), 17);
      JSONObject defaults=item.optJSONObject("reminder_defaults");
      if(defaults!=null) {
        if(!defaults.isNull("event_offset_seconds"))label("新日程默认提醒："+reminderOffset(defaults.getLong("event_offset_seconds")),14);
        if(!defaults.isNull("todo_offset_seconds"))label("新待办默认提醒："+reminderOffset(defaults.getLong("todo_offset_seconds")),14);
        label("仅沿用于之后的新事项；原文指定的提醒优先。",14);
      }
      label(state.equals("paused") ? "已暂停" : state.equals("revoked") ? "已撤销" : item.getBoolean("shadow") ? "观察中" : "自动执行已开启", 14);
      if (!state.equals("revoked")) {
        if (item.getBoolean("shadow")) intentButton(item, "开启自动执行", "enable");
        intentButton(item, state.equals("paused") ? "恢复" : "暂停", state.equals("paused") ? "resume" : "pause");
        intentButton(item, "撤销委托", "revoke");
      }
    }
    label("需要核对", 20);
    JSONArray checks=snapshot.optJSONArray("review");
    if(checks!=null)for(int i=0;i<checks.length();i++){
      JSONObject check=checks.getJSONObject(i);label(check.getString("title"),17);label(check.getString("text"),14);label(check.getString("reason"),14);if(check.has("current_text"))label("当前服务端："+check.getString("current_text"),14);
      JSONArray evidence=check.optJSONArray("evidence");if(evidence!=null)for(int j=0;j<evidence.length();j++)label(evidence.getJSONObject(j).optString("text"),14);
      String reviewPath="/v1/agent/review/"+check.getString("kind")+"/"+check.getString("reference");
      if(queuedPaths.contains(reviewPath)){label("决定已提交，等待后台更新。",14);continue;}
      boolean memory=check.getString("kind").equals("memory");
      boolean failed=check.getString("kind").equals("observation");
      if(failed)content.addView(NativeUi.button(this,"查看原文",()->showEvidence(check),false));
      content.addView(NativeUi.button(this,failed?"重新处理":memory?"确认保存":"保留本机修改",()->decideReview(check,failed?"retry":memory?"accept":"keep_local"),false));
      content.addView(NativeUi.button(this,failed?"跳过本次版本":memory?"拒绝保存":"采用服务端版本",()->decideReview(check,failed||memory?"reject":"keep_server"),false));
    }
    JSONArray work = snapshot.getJSONArray("work");
    workOffset=snapshot.optInt("work_offset",0);
    activityBefore=snapshot.optLong("activity_before",0);
    int pending = 0;
    for (int i = 0; i < work.length(); i++) {
      JSONObject item = work.getJSONObject(i);
      if (!item.getString("state").equals("proposed")) continue;
      pending++;
      label(item.getString("title"), 17);
      label(item.getString("details"), 14);
      if(item.optString("reminder_policy").equals("none"))label("本事项不提醒。",14);
      boolean historical = !item.optString("temporal_status", "current").equals("current");
      if (historical) label("历史核对：" + item.optString("temporal_reason"), 14);
      if(!item.isNull("start_date"))label("全天活动："+item.optString("start_date")+" 至 "+item.optString("end_date")+"（结束日期不包含在内）",14);
      content.addView(NativeUi.button(this, "查看依据", () -> showEvidence(item), false));
      String pendingPath="/v1/personal-assistant/work-items/"+android.net.Uri.encode(item.getString("work_item_id"));
      android.widget.Button editButton=NativeUi.button(this,"修改内容",()->edit(item),false);
      editButton.setEnabled(!queuedPaths.contains(pendingPath)&&!submitting.contains(pendingPath));
      content.addView(editButton);
      reviewButton(item, historical ? "确认仍需处理" : "确认", "confirm");
      reviewButton(item, "不保存", "reject");
    }
    if (pending == 0 && (checks==null||checks.length()==0)) label("暂时没有需要核对的内容。", 14);
    label("正在等待", 20);
    for (int i = 0; i < work.length(); i++) {
      JSONObject item = work.getJSONObject(i);
      if (item.getString("state").matches("waiting|blocked"))
        label(item.getString("title") + "\n" + item.optString("waiting_reason", "需要补充资料或检查授权，可在对话中继续。"), 14);
    }
    if(workOffset>0)content.addView(NativeUi.button(this,"上一页事项",()->{workOffset=Math.max(0,workOffset-30);refresh();},false));
    if(!snapshot.isNull("work_next")&&snapshot.has("work_next"))content.addView(NativeUi.button(this,"下一页事项",()->{workOffset=snapshot.optInt("work_next");refresh();},false));
    label("行动时间线", 20);
    JSONArray activity = snapshot.getJSONArray("activity");
    if (activity.length() == 0) label("尚无处理记录。", 14);
    for (int i = activity.length() - 1; i >= 0; i--) {
      JSONObject item = activity.getJSONObject(i);
      label(item.getString("created_at") + "\n" + item.getString("summary"), 14);
    }
    if(!activityPages.isEmpty())content.addView(NativeUi.button(this,"较新的行动记录",()->{
      activityBefore=activityPages.remove(activityPages.size()-1);refresh();},false));
    if(!snapshot.isNull("activity_next")&&snapshot.has("activity_next"))content.addView(NativeUi.button(this,"更早的行动记录",()->{
      activityPages.add(activityBefore);activityBefore=snapshot.optLong("activity_next");refresh();},false));
  }

  private void showEvidence(JSONObject item) {
    worker.execute(() -> {
      try {
        BackendSettings backend = new BackendSettings(this);
        JSONArray refs = item.getJSONArray("evidence");
        StringBuilder text = new StringBuilder();
        for (int i = 0; i < refs.length(); i++) {
          JSONObject ref = refs.getJSONObject(i);
          JSONObject original = backend.client().request("/v1/knowledge/records/" + android.net.Uri.encode(ref.getString("reference")) + "?session_id="+android.net.Uri.encode(backend.session())+"&revision=" + ref.getInt("revision"), null);
          text.append(original.optString("sender_name")).append("\n").append(original.getString("text")).append("\n\n");
        }
        runOnUiThread(() -> { if (!isFinishing()) new AlertDialog.Builder(this).setTitle("消息依据").setMessage(text.toString()).setPositiveButton("关闭", null).show(); });
      } catch (Exception unavailable) {
        runOnUiThread(() -> Toast.makeText(this, "暂时无法读取这个版本的原文，请联网后重试或在本机来源列表中查看。", Toast.LENGTH_LONG).show());
      }
    });
  }

  private void edit(JSONObject item) {
    LinearLayout fields = new LinearLayout(this);
    fields.setOrientation(LinearLayout.VERTICAL);
    EditText title = new EditText(this), details = new EditText(this);
    title.setText(item.optString("title")); title.setHint("标题");
    details.setText(item.optString("details")); details.setHint("完整内容"); details.setMinLines(3);
    fields.addView(title); fields.addView(details);
    java.util.LinkedHashMap<String,android.widget.Button> times=new java.util.LinkedHashMap<>();
    if(item.optString("kind").equals("event")){
      boolean allDay=!item.isNull("start_date")&&!item.optString("start_date").isEmpty();
      times.put(allDay?"start_date":"starts_at",timeField(fields,"开始",item,allDay?"start_date":"starts_at",allDay));
      times.put(allDay?"end_date":"ends_at",timeField(fields,allDay?"结束（不含此日）":"结束",item,allDay?"end_date":"ends_at",allDay));
    }
    if(item.optString("kind").equals("todo"))times.put("due_date",timeField(fields,"截止日期",item,"due_date",true));
    if(item.optString("kind").matches("todo|event"))times.put("remind_at",timeField(fields,"提醒时刻",item,"remind_at",false));

    AlertDialog dialog = new AlertDialog.Builder(this).setTitle("修改待核对内容").setView(fields).setNegativeButton("取消", null).setPositiveButton("保存修改", null).create();
    dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
      if (title.getText().toString().trim().isEmpty() || title.length() > 256 || details.length() > 2400) { title.setError("请填写标题，内容不要超过 2400 字"); return; }
      try {
        JSONObject changes=new JSONObject().put("title",title.getText().toString()).put("details",details.getText().toString());
        for(java.util.Map.Entry<String,android.widget.Button> field:times.entrySet())changes.put(field.getKey(),field.getValue().getTag());
        queue("/v1/personal-assistant/work-items/" + android.net.Uri.encode(item.getString("work_item_id")), item.getInt("revision"), "edit", changes, item.getString("title"));
        dialog.dismiss();
      } catch (Exception invalid) { title.setError("无法保存，请刷新后重试"); }
    }));
    dialog.show();
  }

  private android.widget.Button timeField(LinearLayout fields,String label,JSONObject item,String key,boolean dateOnly){
    android.widget.Button button=new android.widget.Button(this);Object initial=item.isNull(key)?JSONObject.NULL:item.opt(key);button.setTag(initial);renderTime(button,label,dateOnly);fields.addView(button);
    button.setOnClickListener(view->{
      java.time.ZonedDateTime current=java.time.ZonedDateTime.now();
      try{if(button.getTag()!=JSONObject.NULL)current=dateOnly?java.time.LocalDate.parse(button.getTag().toString()).atStartOfDay(java.time.ZoneId.systemDefault()):java.time.OffsetDateTime.parse(button.getTag().toString()).atZoneSameInstant(java.time.ZoneId.systemDefault());}catch(Exception invalid){/* Picker displays an explicit replacement. */}
      final java.time.ZonedDateTime selected=current;
      android.app.DatePickerDialog picker=new android.app.DatePickerDialog(this,(v,y,m,d)->{
        java.time.LocalDate day=java.time.LocalDate.of(y,m+1,d);
        if(dateOnly){button.setTag(day.toString());renderTime(button,label,true);}
        else new android.app.TimePickerDialog(this,(clock,hour,minute)->{button.setTag(java.time.ZonedDateTime.of(day,java.time.LocalTime.of(hour,minute),java.time.ZoneId.systemDefault()).toOffsetDateTime().toString());renderTime(button,label,false);},selected.getHour(),selected.getMinute(),true).show();
      },selected.getYear(),selected.getMonthValue()-1,selected.getDayOfMonth());
      picker.setButton(android.app.AlertDialog.BUTTON_NEUTRAL,"清除",(dialog,which)->{button.setTag(JSONObject.NULL);renderTime(button,label,dateOnly);});picker.show();
    });return button;
  }

  private void renderTime(android.widget.Button button,String label,boolean dateOnly){
    Object value=button.getTag();String shown="未设置";
    try{if(value!=JSONObject.NULL)shown=dateOnly?value.toString():java.time.OffsetDateTime.parse(value.toString()).atZoneSameInstant(java.time.ZoneId.systemDefault()).format(java.time.format.DateTimeFormatter.ofLocalizedDateTime(java.time.format.FormatStyle.SHORT));}catch(Exception invalid){shown="请重新选择";}
    button.setText(label+"："+shown);
  }

  private void showDraft(JSONObject action) {
    final int ticket=generation;
    worker.execute(()->{
      try(MemoryStore memory=new MemoryStore(this)) {
        BackendSettings backend=new BackendSettings(this);
        String base=backend.base(),session=backend.session();
        KnowledgeStore store=new KnowledgeStore(memory,base);
        JSONObject body=action.getJSONObject("payload"),changes=body.optJSONObject("changes");
        boolean nativeReview=body.has("_native_local_id"),conflict=action.getString("state").equals("conflict");
        JSONObject local=nativeReview?new OrganizerStore(memory).work(body.getString("_native_local_id")):changes;
        String text="本机修改\n"+(local==null?action.getString("label"):draftSummary(local));
        int revision=0;boolean retry=false;
        if(conflict) {
          if(nativeReview&&local!=null) {
            text+="\n\n将使用上面这份本机当前内容重新确认。原文和事项同步后，宁宁仍会核对服务端版本及授权范围。";
            retry=true;
          } else if(!nativeReview) {
            try {
              JSONObject current=backend.client().request(action.getString("path")+"?session_id="+android.net.Uri.encode(session),null);
              revision=current.getInt("revision");retry=true;
              text+="\n\n当前服务端\n"+draftSummary(current)+"\n\n确认后将按这个版本重新提交本机修改，再有变化时仍会进入核对。";
            } catch(Exception unavailable) {text+="\n\n暂时无法读取当前服务端版本。请联网后重新打开，本机内容已保留。";}
          }
        }
        final String message=text;final int expected=revision;final boolean allowRetry=retry;
        runOnUiThread(()->{
          if(isFinishing()||isDestroyed()||ticket!=generation)return;
          try {
            AlertDialog.Builder dialog=new AlertDialog.Builder(this).setTitle(action.getString("label"))
                .setMessage(message).setNegativeButton("保留",null)
                .setNeutralButton("撤回本机修改",(d,w)->discardDraft(action,base));
            if(allowRetry)dialog.setPositiveButton("确认并重新提交",(d,w)->worker.execute(()->{
              try(MemoryStore current=new MemoryStore(this)) {
                BackendSettings now=new BackendSettings(this);
                if(!base.equals(now.base())||!session.equals(now.session()))throw new IllegalStateException("连接已变化");
                RuntimeActions.rebase(new KnowledgeStore(current,base),action.getString("operation"),expected);
                runOnUiThread(this::refresh);
              } catch(Exception changed){runOnUiThread(()->Toast.makeText(this,"未重新提交，请刷新后核对连接或版本。本机修改仍保留。",Toast.LENGTH_LONG).show());}
            }));
            dialog.show();
          }catch(Exception invalid){Toast.makeText(this,"无法读取这项修改",Toast.LENGTH_LONG).show();}
        });
      }catch(Exception invalid){runOnUiThread(()->Toast.makeText(this,"无法读取这项修改，本机内容仍保留。",Toast.LENGTH_LONG).show());}
    });
  }

  private String draftSummary(JSONObject value) {
    StringBuilder text=new StringBuilder(value.optString("title",value.optString("goal","事项")));
    if(!value.optString("details").isEmpty())text.append("\n").append(value.optString("details"));
    if(value.has("state")) {
      String state=value.optString("state");
      text.append("\n状态：").append(state.equals("paused")?"已暂停":state.equals("revoked")?"已撤销":
          state.equals("active")?"生效中":state.equals("completed")?"已完成":state.equals("cancelled")?"已取消":
          state.equals("blocked")?"需要核对":state.equals("waiting")?"正在等待":"待处理");
    }
    if(value.has("allow_writes"))text.append("\n允许动作：").append(value.optBoolean("allow_writes")?"所选范围内可修改":"只读");
    for(String key:new String[]{"start_date","end_date","starts_at","ends_at","due_at","due_date","remind_at"})
      if(value.has(key)&&!value.isNull(key))text.append("\n").append(
          key.startsWith("start")?"开始：":key.startsWith("end")?"结束：":key.equals("remind_at")?"提醒：":"截止：").append(value.optString(key));
    return text.toString();
  }

  private void discardDraft(JSONObject action, String base) {
    worker.execute(()->{
      try(MemoryStore memory=new MemoryStore(this)){RuntimeActions.discard(new KnowledgeStore(memory,base),action.optString("operation"));}
      catch(Exception unavailable){runOnUiThread(()->Toast.makeText(this,"撤回未完成，本机修改仍保留。",Toast.LENGTH_LONG).show());}
      runOnUiThread(this::refresh);
    });
  }

  private String reminderOffset(long seconds) {
    return seconds==0?"到时提醒":"提前 "+(seconds%60==0?seconds/60+" 分钟":seconds+" 秒");
  }

  private void intentButton(JSONObject item, String label, String action) throws Exception {
    String path = "/v1/agent/intents/" + item.getString("intent_id");
    int revision = item.getInt("revision");
    android.widget.Button button=NativeUi.button(this,label,()->queue(path,revision,action,null,label),false);
    button.setEnabled(!queuedPaths.contains(path)&&!submitting.contains(path));
    content.addView(button);
  }

  private void reviewButton(JSONObject item, String label, String action) throws Exception {
    String path = "/v1/personal-assistant/work-items/" + android.net.Uri.encode(item.getString("work_item_id"));
    int revision = item.getInt("revision");
    android.widget.Button button=NativeUi.button(this,label,()->queue(path,revision,action,null,label),false);
    button.setEnabled(!queuedPaths.contains(path)&&!submitting.contains(path));
    content.addView(button);
  }

  private void queue(String path, int revision, String action, JSONObject changes, String label) {
    try {
      BackendSettings backend=new BackendSettings(this);
      JSONObject body=new JSONObject().put("session_id",backend.session()).put("expected_revision",revision).put("action",action);
      if(changes!=null)body.put("changes",changes);
      submit(path,body,label);
    }catch(Exception invalid){Toast.makeText(this,"提交内容无效，请重新核对。",Toast.LENGTH_LONG).show();}
  }

  @Override public void onDestroy() {
    generation++;
    worker.shutdownNow();
    submissions.shutdown(); // Already submitted local writes finish independently of the screen.
    super.onDestroy();
  }
}
