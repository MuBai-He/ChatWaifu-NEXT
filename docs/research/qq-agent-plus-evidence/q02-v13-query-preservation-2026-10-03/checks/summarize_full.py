"""Read-only accounting of all attempts, paired inputs, and retained budgets."""

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

root = Path(sys.argv[1])
expected_logical = int(sys.argv[2]) if len(sys.argv) > 2 else 288


def read(name):
    path = root / name
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def quantiles(values):
    values = sorted(values)
    return {
        "count": len(values),
        "p50_ms": values[math.ceil(len(values) * 0.5) - 1] if values else None,
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1] if values else None,
    }


def aggregate(calls):
    result = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
        values = [(c.get("usage") or {}).get(field) for c in calls]
        reported = [n for n in values if type(n) is int and n >= 0]
        result[field] = {
            "attempts": len(calls),
            "reported_attempts": len(reported),
            "known_subtotal": sum(reported),
            "complete_total": sum(reported) if len(reported) == len(calls) and calls else None,
        }
    return result


rows, events, wires, incomplete = (
    read("results.jsonl"),
    read("provider-rounds.jsonl"),
    read("actual-provider-payloads.jsonl"),
    read("incomplete.jsonl"),
)
starts = [r for r in events if r["event"] == "started"]
finishes = [r for r in events if r["event"] == "finished"]
assert len({r["call_id"] for r in starts}) == len(starts), "duplicate provider starts"
assert len({r["call_id"] for r in finishes}) == len(finishes), "duplicate provider finishes"
assert len({r["sample_key"] for r in rows}) == len(rows), "duplicate completed samples"
# The launcher issues calls serially. Verify that before resolving adjacent
# timestamp windows; the original 100 ms tolerance overlapped reused prompts.
assert len(events) % 2 == 0 and all(
    a["event"] == "started" and b["event"] == "finished" and a["call_id"] == b["call_id"]
    for a, b in zip(events[::2], events[1::2], strict=False)
), "non-serial or unfinished journal"
next_start = {
    r["call_id"]: datetime.fromisoformat(starts[i + 1]["started_at"])
    for i, r in enumerate(starts[:-1])
}
finished_ids = {r["call_id"] for r in finishes}
assert finished_ids <= {r["call_id"] for r in starts}
all_attempts = finishes + [r for r in starts if r["call_id"] not in finished_ids]
wire_map = defaultdict(list)
unmatched = []
for i, wire in enumerate(wires):
    payload = wire["payload"]
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert digest == wire["payload_sha256"]
    stamp = datetime.fromisoformat(wire["captured_at_utc"])
    system_hash = hashlib.sha256(payload["messages"][0]["content"].encode()).hexdigest()
    possible = [
        r
        for r in all_attempts
        if r["system_prompt_sha256"] == system_hash
        and datetime.fromisoformat(r["started_at"]) <= stamp
        and (r["call_id"] not in next_start or stamp < next_start[r["call_id"]])
        and (
            r["call_id"] not in finished_ids
            or stamp
            <= datetime.fromisoformat(r["started_at"])
            + timedelta(milliseconds=r["latency_ms"] + 100)
        )
    ]
    if len(possible) != 1:
        unmatched.append({"wire_index": i, "matching_calls": len(possible)})
    else:
        wire_map[possible[0]["call_id"]].append(i)

by_key = {r["sample_key"]: r for r in rows}
pairs = []
for r in rows:
    if r["variant"] != "baseline":
        continue
    other = by_key.get(r["sample_key"].rsplit(":", 1)[0] + ":candidate")
    if other:
        pairs.append(
            {
                "key": r["sample_key"].rsplit(":", 1)[0],
                "same_user_text": r["user_text"] == other["user_text"],
                "same_state_and_plan": r["input_snapshot"] == other["input_snapshot"],
            }
        )
input_rows = []
for call in all_attempts:
    budget = call.get("input_budget_report") or {}
    actual = (call.get("usage") or {}).get("prompt_tokens")
    estimate = budget.get("estimated_input_tokens")
    input_rows.append(
        {
            "sample_key": call["sample_key"],
            "call_id": call["call_id"],
            "actual_prompt_tokens": actual,
            "reference_estimate": estimate,
            "relative_error_pct": 100 * (estimate - actual) / actual
            if actual and estimate is not None
            else None,
            "omitted_history": budget.get("omitted_history_indices"),
            "omitted_tool_preambles": budget.get("omitted_tool_preamble_indices"),
            "actual_http_attempts": len(wire_map.get(call["call_id"], [])),
        }
    )
summary = {
    "logical_completed": len(rows),
    "expected_logical": expected_logical,
    "unique_completed": len(by_key),
    "provider_started": len(starts),
    "provider_finished": len(finishes),
    "provider_unfinished": len(starts) - len(finishes),
    "actual_http_requests": len(wires),
    "unmatched_http_requests": unmatched,
    "provider_attempts_without_wire": [
        r["call_id"] for r in all_attempts if not wire_map.get(r["call_id"])
    ],
    "http_model_ids": sorted({r["payload"]["model"] for r in wires}),
    "models_correct": bool(wires)
    and all(r["payload"]["model"] == "gemini-3.8-flash-high" for r in wires),
    "all_attempt_usage": aggregate(all_attempts),
    "provider_errors": dict(
        Counter(r.get("error_type") for r in all_attempts if r.get("error_type"))
    ),
    "incomplete_records": len(incomplete),
    "still_missing_samples": sorted({r["sample_key"] for r in incomplete} - set(by_key)),
    "paired_samples": len(pairs),
    "paired_input_mismatches": [
        r for r in pairs if not r["same_user_text"] or not r["same_state_and_plan"]
    ],
    "reply_origins": dict(Counter(r["runtime_source_trace"]["reply_origin"] for r in rows)),
    "reply_finish_reasons": dict(Counter(r["finish_reason"] for r in rows)),
    "latency": quantiles([r["latency_ms"] for r in rows]),
    "variants": {},
    "source_outcomes": dict(
        Counter(
            t["skill_id"] + ":" + t["state"]
            for r in rows
            for t in r["runtime_source_trace"]["tool_runs"]
        )
    ),
    "history_omissions": sum(len(x["omitted_history"] or []) for x in input_rows),
    "tool_preamble_omissions": sum(len(x["omitted_tool_preambles"] or []) for x in input_rows),
    "quality_approved": False,
    "quality_status": (
        "requires direct version-masked and factual review; these are accounting checks only"
    ),
}
for variant in ["baseline", "candidate"]:
    vr = [r for r in rows if r["variant"] == variant]
    calls = [c for c in all_attempts if c["sample_key"].endswith(":" + variant)]
    summary["variants"][variant] = {
        "logical_completed": len(vr),
        "all_attempt_usage": aggregate(calls),
        "latency": quantiles([r["latency_ms"] for r in vr]),
        "scenarios": dict(Counter(r["scenario_id"] for r in vr)),
    }
errors = [x["relative_error_pct"] for x in input_rows if x["relative_error_pct"] is not None]
summary["reference_estimation_error_pct_range"] = [min(errors), max(errors)] if errors else None
for name, obj in [
    ("accounting.json", summary),
    ("paired-input-audit.json", pairs),
    ("input-budget-audit.json", input_rows),
    ("wire-attempt-map.json", dict(wire_map)),
]:
    (root / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
