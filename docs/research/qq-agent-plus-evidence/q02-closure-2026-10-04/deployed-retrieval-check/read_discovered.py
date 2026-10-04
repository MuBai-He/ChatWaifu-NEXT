import asyncio,json,sys,time,hashlib
from pathlib import Path
ROOT=Path("/tmp/cw2-q02-scope-blocks-v39-eval")
sys.path[:0]=[str(ROOT/"services/runtime/src"),str(ROOT/"packages/protocol-python/src"),str(ROOT/"packages/model-worker-sdk-python/src"),"/tmp/cw2-q02-eval-deps",str(ROOT)]
from pydantic import SecretStr
from chatwaifu_runtime.config.settings import PublicWebConfig
from chatwaifu_runtime.runtime_skills.public_web import PublicWebReader
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
OUT=Path("/tmp/cw2-q02-deployed-retrieval-v40-20261004")
assert not (OUT/"discovered-read.json").exists()
settings={}
for line in Path("/home/mubai/.local/share/chatwaifu-server/config/public-web.env").read_text().splitlines():
 if line.startswith("CHATWAIFU_PUBLIC_WEB__") and "=" in line:
  name,value=line.split("=",1);settings[name]=value.strip().strip(chr(34)).strip(chr(39))
token=settings.get("CHATWAIFU_PUBLIC_WEB__CRAWL4AI_API_TOKEN")
assert token,"Reader token unavailable; no read attempted"
config=PublicWebConfig(search_provider="searxng",reader_provider="crawl4ai",searxng_endpoint="http://127.0.0.1:18080",crawl4ai_endpoint="http://127.0.0.1:11235",crawl4ai_api_token=SecretStr(token),timeout_seconds=30,crawl4ai_builtin_fallback=False)
reader=PublicWebReader(provider_config=config)
query=json.loads((OUT/"search-2.json").read_text())
url=next(row["url"] for row in query["results"] if row["url"]=="https://raft.github.io/raft.pdf")
async def main():
 args={"url":url,"focus":"randomized election timeout","max_characters":6000,"dns_resolver":"cloudflare","fresh":True}
 row={"arguments":args,"search_result_rank":next(i for i,x in enumerate(query["results"],1) if x["url"]==url),"adapter_source_snapshot":"v39","service_endpoint":"http://127.0.0.1:11235","builtin_fallback_enabled":False,"model_calls":0}
 start=time.monotonic()
 try:row.update(ok=True,result=await reader.read(args))
 except SkillExecutionError as error:row.update(ok=False,error_code=error.structured.code,retryable=error.structured.retryable)
 except Exception as error:row.update(ok=False,unclassified_error_type=type(error).__name__)
 row["elapsed_ms"]=round((time.monotonic()-start)*1000)
 text=json.dumps(row,ensure_ascii=False,sort_keys=True,indent=2)
 assert token not in text
 (OUT/"discovered-read.json").write_text(text+"\n")
 files={}
 for rel in ["services/runtime/src/chatwaifu_runtime/runtime_skills/public_web.py","services/runtime/src/chatwaifu_runtime/runtime_skills/public_web_providers.py","services/runtime/src/chatwaifu_runtime/runtime_skills/transports.py"]:
  files[rel]=hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
 (OUT/"reader-source-sha256.json").write_text(json.dumps({"files":files},sort_keys=True,indent=2)+"\n")
 exports=list(OUT.glob("*.json"));assert all(token not in f.read_text() for f in exports)
 p=Path("/home/mubai/chatwaifu-server/public-web/searxng/settings.yml")
 before=json.loads((OUT/"engine-inventory.json").read_text())["settings_sha256"]
 check={"credential_values_checked":1,"files_scanned":len(exports),"credential_leaks":0,"production_config_mutations":0,"searxng_settings_unchanged":hashlib.sha256(p.read_bytes()).hexdigest()==before,"actual_search_calls":3,"actual_read_calls":1,"model_calls":0}
 (OUT/"privacy-integrity.json").write_text(json.dumps(check,sort_keys=True,indent=2)+"\n")
 print(json.dumps({k:v for k,v in row.items() if k!="result"},ensure_ascii=False),flush=True)
 print(json.dumps(check),flush=True)
asyncio.run(main())
