"""Same-input next-action diagnostic; returned functions are not executed."""

import copy
import json
import time
from pathlib import Path

import requests

root = Path("/tmp/cw2-q02-v13-source-smoke-20261003/results")
original = next(
    r["payload"]
    for r in map(json.loads, (root / "actual-provider-payloads.jsonl").read_text().splitlines())
    if r["payload"].get("tools")
    and len([m for m in r["payload"]["messages"] if m["role"] == "tool"]) == 4
)
assert original["model"] == "gemini-3.8-flash-high"
addition = """
When a search result describes a later change or a condition different from an
already read document, treat it as an unresolved lead. Do not accept the snippet
as fact, but do not silently discard it and present the older document as current.
Use the distinctive title, named issuer or changed subject in that result to
locate and read its original announcement. Keep the search focused on the user's
subject; a broad query about the entire field may miss the specific change.
If an excerpt refers to another section needed for a claim, read that section or
omit the unverified detail. A URL read once does not verify its omitted sections.
Resolve these gaps before finalizing; if the available calls cannot resolve them,
explain the specific unresolved scope in the user-facing answer.
"""
out = Path("/tmp/cw2-q02-source-conflict-probe-20261003")
out.mkdir(exist_ok=True)
(out / "addition.txt").write_text(addition)
key = json.loads(
    Path("/home/mubai/.local/share/chatwaifu-server/config/model-secrets.json").read_text()
)["chat"]
session = requests.Session()
session.trust_env = False
for repeat in range(2):
    for variant in ["original", "conflict_followup"]:
        if (out / "results.jsonl").exists() and any(
            (r["repeat"], r["variant"]) == (repeat, variant)
            for r in map(json.loads, (out / "results.jsonl").read_text().splitlines())
        ):
            continue
        payload = copy.deepcopy(original)
        payload["stream"] = False
        payload.pop("stream_options", None)
        if variant == "conflict_followup":
            assert payload["messages"][-1]["role"] == "system"
            payload["messages"][-1]["content"] += addition
        started = time.monotonic()
        response = session.post(
            "https://mubai.website:8318/v1/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json=payload,
            timeout=180,
        )
        body = response.json()
        message = body.get("choices", [{}])[0].get("message") or {}
        # Record visible answers and token counts; never archive hidden reasoning.
        if "reasoning_content" in message:
            message["reasoning_content"] = None
        row = {
            "repeat": repeat,
            "variant": variant,
            "http_status": response.status_code,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "request": payload,
            "response": body,
        }
        assert key not in json.dumps(row)
        with (out / "results.jsonl").open("a") as target:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "repeat": repeat,
                    "variant": variant,
                    "status": response.status_code,
                    "tool_calls": message.get("tool_calls"),
                    "text": (message.get("content") or "")[:120],
                    "usage": body.get("usage"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
