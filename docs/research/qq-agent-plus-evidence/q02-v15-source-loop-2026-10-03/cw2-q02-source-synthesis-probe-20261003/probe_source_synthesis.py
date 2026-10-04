"""Replay a frozen checklist revision with explicit whole-task coverage; no tools."""
import copy
import json
import time
from pathlib import Path

import requests

root=Path('/tmp/cw2-q02-v14-source-smoke-20261003/results')
wires=[json.loads(l)['payload'] for l in (root/'actual-provider-payloads.jsonl').read_text().splitlines()]
original=wires[-1]
assert original['model']=='gemini-3.8-flash-high' and not original.get('tools')
assert 'runtime_source_answer_revision' in original['messages'][-1]['content']
addition='''5. 用户要求合并或整理此前内容时，回看完整对话中原先要求的项目、各个独立方案、行动、预计用时、数量和必要注意事项，核对当前草稿是否漏项并补齐；简明只缩短表达，不能把多个方案合并成没有具体内容的一项。对话中的计划建议仍标为建议，不能升级成强制规定。此前助手自己添加的未核实开放时间、准入要求等外部断言不是可靠来源，应删除或明确待核实。不要在清单末尾另加未请求的新建议。\n'''
out=Path('/tmp/cw2-q02-source-synthesis-probe-20261003');out.mkdir(exist_ok=True)
(out/'addition.txt').write_text(addition)
key=json.loads(Path('/home/mubai/.local/share/chatwaifu-server/config/model-secrets.json').read_text())['chat']
s=requests.Session();s.trust_env=False
for repeat in range(2):
 for variant in ['original','whole_task_coverage']:
  if (out/'results.jsonl').exists() and any((r['repeat'],r['variant'])==(repeat,variant) for r in map(json.loads,(out/'results.jsonl').read_text().splitlines())):continue
  p=copy.deepcopy(original);p['stream']=False;p.pop('stream_options',None)
  if variant=='whole_task_coverage':p['messages'][-1]['content']=p['messages'][-1]['content'].replace('只输出修订后的完整答复',addition+'只输出修订后的完整答复',1)
  start=time.monotonic();response=s.post('https://mubai.website:8318/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=p,timeout=180);data=response.json()
  for choice in data.get('choices',[]):
   if isinstance(choice.get('message'),dict):choice['message']['reasoning_content']=None
  row={'repeat':repeat,'variant':variant,'http_status':response.status_code,'latency_ms':round((time.monotonic()-start)*1000),'request':p,'response':data}
  assert key not in json.dumps(row)
  with (out/'results.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
  answer=(data.get('choices',[{}])[0].get('message') or {}).get('content') or ''
  print(json.dumps({'repeat':repeat,'variant':variant,'status':response.status_code,'text':answer[-1000:],'usage':data.get('usage')},ensure_ascii=False),flush=True)
