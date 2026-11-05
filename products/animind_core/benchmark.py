"""Compare configured Animind script and image providers without leaking keys.

Only providers whose key environment variable is present are called. Missing
or config-only image adapters produce explicit dry-run telemetry rows. Paid
image generation is globally capped at USD 0.30 for this command.

Run::

    python -m products.animind_core.benchmark --output /tmp/animind-benchmark
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from production.providers.llm import cheap_llm

from . import characters, image_models, llm_providers, script, settings, telemetry

PROMPT = """Write a short coherent English argument about teaching students to verify AI claims.
Return JSON only: {"lines":[{"i":0,"text":"hook"},{"i":1,"text":"argument"},{"i":2,"text":"close"}]}.
Each line must be one complete sentence of at most 14 words."""

CHEAP_MODELS = (
    ("deepseek", "deepseek-flash"),
    ("deepinfra", "deepseek-ai/DeepSeek-V4-Flash"),
    ("openrouter", "deepseek/deepseek-v4.1-flash"),
)


def _script_qa(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        lines = data.get("lines") or []
        ok = len(lines) == 3 and all(
            isinstance(line, dict) and str(line.get("text") or "").strip()
            and len(str(line["text"]).split()) <= 14
            for line in lines
        )
        return {"pass": bool(ok), "problems": [] if ok else ["invalid three-line script"]}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"pass": False, "problems": ["invalid JSON"]}


def _artifact(output: Path, slug: str, payload: dict[str, Any]) -> Path:
    path = output / f"{slug}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _cheap_llm_rows(output: Path) -> list[dict[str, Any]]:
    rows = []
    for provider, model in CHEAP_MODELS:
        key_env = cheap_llm.BASES[provider][1]
        if not (os.getenv(key_env) or "").strip():
            row = telemetry.record(
                "benchmark-llm", provider=provider, model=model,
                status="dry-run:missing-key", metadata={"missing": key_env},
            )
            rows.append(row)
            continue
        started = time.perf_counter()
        try:
            result = cheap_llm.complete(
                PROMPT, json_mode=True, max_tokens=180, temperature=0.2,
                source="animind_core.benchmark", chain=[(provider, model)],
            )
            qa = _script_qa(result.text)
            artifact = _artifact(output, f"llm-{provider}", {
                "provider": result.provider, "model": result.model,
                "text": result.text, "qa": qa,
            })
            row = telemetry.record(
                "benchmark-llm", provider=result.provider, model=result.model,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_usd=result.cost_usd, latency_ms=(time.perf_counter() - started) * 1000,
                script_qa=qa, artifact=artifact,
                status="ok" if qa["pass"] else "qa-failed",
            )
        except Exception as exc:  # noqa: BLE001 - report provider comparison failure
            row = telemetry.record(
                "benchmark-llm", provider=provider, model=model,
                latency_ms=(time.perf_counter() - started) * 1000,
                status="failed", metadata={"errorType": type(exc).__name__},
            )
        rows.append(row)
    return rows


def _gemini_row(output: Path) -> dict[str, Any]:
    if not settings.gemini_available():
        return telemetry.record(
            "benchmark-llm", provider="google", model=llm_providers.GEMINI_MODEL,
            status="dry-run:missing-key", metadata={"missing": "GEMINI_API_KEY"},
        )
    started = time.perf_counter()
    try:
        result = llm_providers.GeminiProvider(script._gemini).complete(PROMPT)
        text = json.dumps({"lines": result.lines}, ensure_ascii=False)
        qa = _script_qa(text)
        artifact = _artifact(output, "llm-gemini", {
            "provider": result.provider, "model": result.model,
            "lines": result.lines, "qa": qa,
        })
        return telemetry.record(
            "benchmark-llm", provider=result.provider, model=result.model,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cost_usd=result.cost_usd, latency_ms=(time.perf_counter() - started) * 1000,
            script_qa=qa, artifact=artifact,
            status="ok" if qa["pass"] else "qa-failed",
        )
    except Exception as exc:  # noqa: BLE001
        return telemetry.record(
            "benchmark-llm", provider="google", model=llm_providers.GEMINI_MODEL,
            latency_ms=(time.perf_counter() - started) * 1000,
            status="failed", metadata={"errorType": type(exc).__name__},
        )


def _image_rows(output: Path, budget_usd: float, allow_paid: bool) -> list[dict[str, Any]]:
    rows = []
    spent = 0.0
    for model_id, spec in image_models.models().items():
        configured = bool(spec.keyEnv and (os.getenv(spec.keyEnv) or "").strip())
        if not configured:
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="dry-run:missing-key",
                metadata={"modelId": model_id, "missing": spec.keyEnv, "budgetUsd": budget_usd},
            ))
            continue
        if not allow_paid:
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="dry-run:paid-disabled",
                metadata={"modelId": model_id, "budgetUsd": budget_usd},
            ))
            continue
        if spec.provider != "gemini" or not spec.implemented:
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="dry-run:adapter-not-enabled",
                metadata={"modelId": model_id, "budgetUsd": budget_usd},
            ))
            continue
        estimate = min(0.30, 7 * spec.usdPerImage)
        if spent + estimate > budget_usd:
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="dry-run:budget-cap",
                metadata={"modelId": model_id, "estimatedUsd": estimate, "budgetUsd": budget_usd},
            ))
            continue
        # The existing Aiwake-backed rig path creates master/front/parts,
        # performs QA, and stops itself at the same hard cap.
        os.environ["REEL_REEL_BUDGET_USD"] = f"{budget_usd:.2f}"
        os.environ["MAX_GOOGLE_COST_PER_RUN_USD"] = f"{budget_usd:.2f}"
        started = time.perf_counter()
        character = characters.create_character(
            {
                "type": "rig", "name": f"Benchmark {model_id}", "reuse": False,
                "source": {"kind": "prompt", "prompt": "friendly debate host, simple cartoon portrait"},
            },
            "file://benchmark",
        )
        actual = float((character.get("qa") or {}).get("costUsd") or 0.0)
        spent += actual
        skin = characters.puppets_dir() / character["id"]
        rows.append(telemetry.record(
            "benchmark-image", provider=spec.provider, model=spec.model,
            images=sum(int(v) for v in ((character.get("qa") or {}).get("attempts") or {}).values() if isinstance(v, int)),
            cost_usd=actual, latency_ms=(time.perf_counter() - started) * 1000,
            image_qa=character.get("qa"), artifact=skin / "views_contact_sheet.png",
            status="ok", metadata={"modelId": model_id, "budgetUsd": budget_usd},
        ))
    return rows


def run(output: Path, *, image_budget_usd: float = 0.30, allow_paid_images: bool = True) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    budget = max(0.0, min(0.30, float(image_budget_usd)))
    rows = [*_cheap_llm_rows(output), _gemini_row(output), *_image_rows(output, budget, allow_paid_images)]
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "output": str(output),
        "imageBudgetUsd": budget,
        "rows": rows,
        "summary": telemetry.summarize(rows),
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "telemetry.md").write_text(telemetry.markdown(rows) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=settings.data_dir() / "benchmarks" / "latest")
    parser.add_argument("--image-budget-usd", type=float, default=0.30)
    parser.add_argument("--dry-run-images", action="store_true")
    args = parser.parse_args(argv)
    report = run(
        args.output, image_budget_usd=args.image_budget_usd,
        allow_paid_images=not args.dry_run_images,
    )
    print(telemetry.markdown(report["rows"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
