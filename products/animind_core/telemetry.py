"""Durable JSONL telemetry for Animind model tests and renders.

Records live under ``ANIMIND_DATA_DIR/telemetry`` and therefore never enter
git.  The module deliberately accepts only aggregate metadata: provider/model,
usage, cost, latency, QA summaries and artifact paths.  Prompts, API keys and
response bodies are not recorded.

Summary command::

    python -m products.animind_core.telemetry summary
"""
from __future__ import annotations

import argparse
import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import settings

_LOCK = threading.Lock()


def directory() -> Path:
    path = settings.data_dir() / "telemetry"
    path.mkdir(parents=True, exist_ok=True)
    return path


def path() -> Path:
    return directory() / "events.jsonl"


def record(
    operation: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    images: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    script_qa: dict[str, Any] | None = None,
    frame_qa: dict[str, Any] | None = None,
    image_qa: dict[str, Any] | None = None,
    artifact: str | Path | None = None,
    status: str = "ok",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one secret-free event and return the normalized row."""
    cached_hit = bool((metadata or {}).get("cached"))
    priced = 0.0 if cached_hit else float(cost_usd or 0.0)
    if cached_hit:
        input_tokens = 0
        output_tokens = 0
    elif priced <= 0 and (input_tokens or output_tokens):
        from .llm_providers import token_cost_usd  # noqa: PLC0415

        priced = token_cost_usd(provider or "", model or "", int(input_tokens or 0), int(output_tokens or 0)) or priced
    row = {
        "schema": 1,
        "at": datetime.now(timezone.utc).isoformat(),
        "operation": str(operation),
        "provider": provider or "offline",
        "model": model or "none",
        "inputTokens": max(0, int(input_tokens or 0)),
        "outputTokens": max(0, int(output_tokens or 0)),
        "images": max(0, int(images or 0)),
        "costUsd": round(max(0.0, priced), 8),
        "latencyMs": round(max(0.0, float(latency_ms or 0.0)), 2),
        "status": str(status),
        "scriptQA": script_qa or None,
        "frameQA": frame_qa or None,
        "imageQA": image_qa or None,
        "artifact": str(artifact) if artifact else None,
        "metadata": metadata or {},
    }
    target = path()
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _LOCK:
        with target.open("a", encoding="utf-8") as stream:
            stream.write(line)
    return row


def read(source: Path | None = None) -> list[dict[str, Any]]:
    source = source or path()
    if not source.is_file():
        return []
    rows = []
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def summarize(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "runs": 0, "inputTokens": 0, "outputTokens": 0, "images": 0,
            "costUsd": 0.0, "latencyMs": 0.0, "scriptPass": 0,
            "scriptChecks": 0, "framePass": 0, "frameChecks": 0,
            "imageScores": [], "artifacts": 0, "statuses": {},
        }
    )
    for row in rows:
        key = (
            str(row.get("operation") or "unknown"),
            str(row.get("provider") or "offline"),
            str(row.get("model") or "none"),
        )
        group = groups[key]
        group["runs"] += 1
        status = str(row.get("status") or "unknown")
        group["statuses"][status] = group["statuses"].get(status, 0) + 1
        for field in ("inputTokens", "outputTokens", "images"):
            group[field] += int(row.get(field) or 0)
        group["costUsd"] += float(row.get("costUsd") or 0.0)
        group["latencyMs"] += float(row.get("latencyMs") or 0.0)
        if row.get("artifact"):
            group["artifacts"] += 1
        for field, yes, checks in (
            ("scriptQA", "scriptPass", "scriptChecks"),
            ("frameQA", "framePass", "frameChecks"),
        ):
            qa = row.get(field)
            if isinstance(qa, dict):
                group[checks] += 1
                group[yes] += int(bool(qa.get("pass")))
        image_qa = row.get("imageQA")
        if isinstance(image_qa, dict) and image_qa.get("score") is not None:
            group["imageScores"].append(float(image_qa["score"]))
    result = []
    for (operation, provider, model), group in sorted(groups.items()):
        runs = group["runs"]
        result.append({
            "operation": operation,
            "provider": provider,
            "model": model,
            "runs": runs,
            "status": ", ".join(
                f"{status}×{count}" if count > 1 else status
                for status, count in sorted(group["statuses"].items())
            ),
            "inputTokens": group["inputTokens"],
            "outputTokens": group["outputTokens"],
            "images": group["images"],
            "costUsd": round(group["costUsd"], 8),
            "avgLatencyMs": round(group["latencyMs"] / runs, 2),
            "scriptQA": f"{group['scriptPass']}/{group['scriptChecks']}" if group["scriptChecks"] else "—",
            "frameQA": f"{group['framePass']}/{group['frameChecks']}" if group["frameChecks"] else "—",
            "imageQAScore": (
                round(sum(group["imageScores"]) / len(group["imageScores"]), 1)
                if group["imageScores"] else "—"
            ),
            "artifacts": group["artifacts"],
        })
    return result


def markdown(rows: Iterable[dict[str, Any]]) -> str:
    summary = summarize(rows)
    headers = (
        "operation", "provider", "model", "status", "runs", "tokens in/out", "images",
        "cost USD", "avg ms", "scriptQA", "frameQA", "image QA", "artifacts",
    )
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in summary:
        lines.append("| " + " | ".join(map(str, (
            row["operation"], row["provider"], row["model"], row["status"], row["runs"],
            f"{row['inputTokens']}/{row['outputTokens']}", row["images"],
            f"{row['costUsd']:.8f}", row["avgLatencyMs"], row["scriptQA"],
            row["frameQA"], row["imageQAScore"], row["artifacts"],
        ))) + " |")
    return "\n".join(lines) if summary else "No telemetry rows."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    summary = sub.add_parser("summary", help="print a provider/model comparison table")
    summary.add_argument("--path", type=Path, default=None, help="alternate JSONL source")
    summary.add_argument("--json", action="store_true", help="emit summary JSON")
    args = parser.parse_args(argv)
    rows = read(args.path)
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2) if args.json else markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
