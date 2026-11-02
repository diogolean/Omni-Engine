"""Compare one English monologue, one English debate, and one character across providers.

Writes a Markdown cost and quality table plus images under ``ANIMIND_DATA_DIR``.
Nothing is written into the git checkout. Without provider keys every row is a
mock and the report says real results were not produced.

Run on a machine that has a ``.env`` with the keys::

    python -m products.animind_core.bench --providers deepseek,gemini,openrouter,together-flux-schnell,openrouter-qwen-image,openrouter-flux-kontext
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import avatar, director, image_models, llm_providers, settings, telemetry

TOPIC = "Why students should verify claims made by AI"
CHARACTER = "a calm teacher with round glasses and a plain shirt"
STYLE = "stick"
REAL_NOTICE = (
    "Real results were not produced. This run had no usable provider keys, "
    "so every script and image row is a mock."
)


def _split(providers: list[str]) -> tuple[list[str], list[str]]:
    scripts: list[str] = []
    images: list[str] = []
    known = image_models.models()
    for raw in providers:
        name = raw.strip().lower()
        if not name:
            continue
        if name in {"deepseek", "gemini", "google", "openrouter"}:
            scripts.append("gemini" if name == "google" else name)
        if name in known:
            images.append(name)
        elif name in {"gemini", "google"}:
            images.append("nano-banana")
        elif name == "together":
            images.append("together-flux-schnell")
        elif name == "openrouter":
            images.extend(["openrouter-qwen-image", "openrouter-flux-kontext"])
        elif name == "qwen":
            images.append("openrouter-qwen-image")
        elif name == "flux":
            images.append("openrouter-flux-kontext")
    return _unique(scripts), _unique(images)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _script_ready(name: str) -> bool:
    if settings.force_mock():
        return False
    if name == "deepseek":
        return llm_providers.DeepSeekProvider().available()
    if name in {"gemini", "google"}:
        return settings.gemini_available()
    if name == "openrouter":
        return llm_providers.OpenRouterProvider().available()
    return False


def _one_script(output: Path, provider: str, names: list[str], template: str) -> dict[str, Any]:
    ready = _script_ready(provider)
    result = director.direct(
        TOPIC, names, template, lang="en-US",
        use_llm=ready, only_provider=provider if ready else None,
    )
    path = output / "scripts" / f"{provider}-{template}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"plan": result["plan"], "scenes": result["scenes"], "shots": result["shots"], "scriptQA": result["scriptQA"]},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    if not ready:
        status = "dry-run:missing-key"
    elif result["provider"] == "offline":
        status = "failed"
    elif result["scriptQA"]["pass"]:
        status = "ok"
    else:
        status = "qa-failed"
    missing = {
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(provider, "")
    return telemetry.record(
        "benchmark-script", provider=result["provider"] if status == "ok" else provider,
        model=str(result.get("model") or "offline"),
        input_tokens=int(result["inputTokens"]), output_tokens=int(result["outputTokens"]),
        cost_usd=float(result["costUsd"]) if status != "dry-run:missing-key" else 0.0,
        script_qa=result["scriptQA"], artifact=path, status=status,
        metadata={
            "template": template, "language": "en-US", "topic": TOPIC,
            "missing": missing if status.startswith("dry-run") else "",
            "real": status in {"ok", "qa-failed"},
        },
    )


def _image_rows(output: Path, model_ids: list[str], budget: image_models.RunBudget) -> list[dict[str, Any]]:
    rows = []
    image_dir = output / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for model_id in model_ids:
        spec = image_models.selected(model_id)
        live = spec.available() and not settings.force_mock()
        skin = output / "avatars" / model_id
        if live and not budget.allow(spec.usdPerImage):
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="dry-run:budget-cap",
                metadata={"modelId": model_id, "budgetUsd": budget.cap_usd, "real": False},
            ))
            continue
        try:
            built = avatar.create_avatar(
                CHARACTER, STYLE, kind="stick", skin=skin, url=lambda path: str(path),
                model_id=model_id if live else None, budget=budget, allow_mock=not live,
            )
        except image_models.BudgetExceeded:
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="dry-run:budget-cap",
                metadata={"modelId": model_id, "budgetUsd": budget.cap_usd, "real": False},
            ))
            continue
        except Exception as exc:  # noqa: BLE001 - one provider must not abort the table
            rows.append(telemetry.record(
                "benchmark-image", provider=spec.provider, model=spec.model,
                status="failed", metadata={"modelId": model_id, "errorType": type(exc).__name__, "real": False},
            ))
            continue
        portrait = skin / "character.png"
        copied = image_dir / f"{model_id}.png"
        if portrait.is_file():
            shutil.copy2(portrait, copied)
        status = "ok" if built.get("realImage") else "mock"
        qa = built.get("qa") or {}
        rows.append(telemetry.record(
            "benchmark-image", provider=str(built.get("generator") or spec.provider),
            model=str(built.get("model") or spec.model),
            images=0 if status == "mock" else int((qa.get("attempts") or {}).get("left", 0)) + int((qa.get("attempts") or {}).get("front", 0)),
            cost_usd=float(qa.get("costUsd") or 0.0),
            image_qa=qa, artifact=copied, status=status,
            metadata={
                "modelId": model_id,
                "character": CHARACTER,
                "style": STYLE,
                "views": sorted((built.get("views") or {})),
                "visemes": sorted(((built.get("views") or {}).get("left") or {}).get("visemes") or {}),
                "real": status == "ok",
                "missing": "" if live else spec.keyEnv,
                "cached": bool(built.get("cached")),
            },
        ))
    return rows


def _markdown(rows: list[dict[str, Any]], *, real: bool, budget: float) -> str:
    lines = []
    if not real:
        lines.append(REAL_NOTICE)
        lines.append("")
    lines.append(f"Same English topic: {TOPIC}")
    lines.append(f"Same character: {CHARACTER} ({STYLE})")
    lines.append(f"Run cap: US${budget:.2f}")
    lines.append("")
    lines.append(telemetry.markdown(rows))
    lines.append("")
    lines.append("| provider | model | status | template or views | cost USD | script QA | image score |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        meta = row.get("metadata") or {}
        detail = meta.get("template") or ",".join(meta.get("views") or [])
        qa = row.get("scriptQA") or {}
        image = row.get("imageQA") or {}
        script = "pass" if qa.get("pass") else ("fail" if qa else "—")
        score = image.get("score", "—")
        lines.append(
            f"| {row.get('provider')} | {row.get('model')} | {row.get('status')} | {detail} | "
            f"{float(row.get('costUsd') or 0):.4f} | {script} | {score} |"
        )
    lines.append("")
    return "\n".join(lines)


def run(
    providers: list[str],
    output: Path | None = None,
    *,
    budget_usd: float | None = None,
) -> dict[str, Any]:
    output = output or (settings.data_dir() / "benchmarks" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True, exist_ok=True)
    scripts, images = _split(providers)
    budget = image_models.run_budget(budget_usd)
    script_rows = []
    for name in scripts:
        script_rows.append(_one_script(output, name, ["Ada"], "monologue"))
        script_rows.append(_one_script(output, name, ["Ada", "Ben"], "debate"))
    image_rows = _image_rows(output, images, budget)
    rows = [*script_rows, *image_rows]
    real = any(bool((row.get("metadata") or {}).get("real")) for row in rows)
    text = _markdown(rows, real=real, budget=budget.cap_usd)
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "output": str(output),
        "realResults": real,
        "notice": None if real else REAL_NOTICE,
        "topic": TOPIC,
        "character": CHARACTER,
        "language": "en-US",
        "budgetUsd": budget.cap_usd,
        "spentUsd": budget.spent_usd,
        "rows": rows,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text(text, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--providers",
        default="deepseek,gemini,openrouter,together-flux-schnell,openrouter-qwen-image,openrouter-flux-kontext",
        help="Comma-separated script and image providers",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    args = parser.parse_args(argv)
    report = run([part.strip() for part in args.providers.split(",")], args.output, budget_usd=args.budget_usd)
    print((Path(report["output"]) / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
