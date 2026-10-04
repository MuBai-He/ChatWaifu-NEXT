import json, os, runpy, sys
from pathlib import Path
root = Path('/tmp/cw2-q02-source-revision-v11-eval')
for line in Path('/home/mubai/.local/share/chatwaifu-server/config/public-web.env').read_text().splitlines():
    if line.startswith('CHATWAIFU_PUBLIC_WEB__') and '=' in line:
        name, value = line.split('=', 1)
        os.environ[name] = value.strip().strip(chr(34)).strip(chr(39))
key = json.loads(Path('/home/mubai/.local/share/chatwaifu-server/config/model-secrets.json').read_text()).get('chat')
if not isinstance(key, str) or not key:
    raise RuntimeError('Configured chat credential unavailable')
os.environ['OPENAI_API_KEY'] = key
os.environ['CHATWAIFU_PUBLIC_WEB__CRAWL4AI_ENDPOINT'] = 'http://127.0.0.1:11236'
os.environ['CHATWAIFU_PUBLIC_WEB__SEARXNG_ENDPOINT'] = 'http://127.0.0.1:18081'
sys.path[:0] = [str(root/'services/runtime/src'), str(root/'packages/protocol-python/src'), str(root/'packages/model-worker-sdk-python/src'), '/tmp/cw2-q02-eval-deps', str(root)]
sys.argv = [str(root/'tools/evaluate_character_scenarios.py'), *sys.argv[1:]]
os.chdir(root)
# Capture only synthetic model JSON at the actual HTTP stream boundary.
# Never record request headers, environment variables or authentication.
import hashlib
from datetime import datetime, timezone
import httpx2
wire_path = Path('/tmp/cw2-q02-v11-source-smoke-20261003/results/actual-provider-payloads.jsonl')
original_stream = httpx2.AsyncClient.stream
def capture_stream(self, method, url, **kwargs):
    payload = kwargs.get('json')
    if method.upper() == 'POST' and str(url).endswith('/chat/completions') and isinstance(payload, dict):
        wire_path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with wire_path.open('a') as target:
            target.write(json.dumps({'captured_at_utc':datetime.now(timezone.utc).isoformat(), 'payload_sha256':digest, 'payload':payload},ensure_ascii=False)+'\n')
    return original_stream(self, method, url, **kwargs)
httpx2.AsyncClient.stream = capture_stream
runpy.run_path(sys.argv[0], run_name='__main__')
