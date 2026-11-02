import json

from products.animind_core import benchmark, image_models, script, telemetry


def test_jsonl_record_and_comparative_summary_stay_outside_git(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    artifact = tmp_path / "proof.mp4"
    telemetry.record(
        "render", provider="deepseek", model="deepseek-flash",
        input_tokens=100, output_tokens=20, cost_usd=0.0001, latency_ms=125,
        script_qa={"pass": True}, frame_qa={"pass": True}, artifact=artifact,
    )
    telemetry.record(
        "render", provider="deepseek", model="deepseek-flash",
        input_tokens=80, output_tokens=15, cost_usd=0.00008, latency_ms=75,
        script_qa={"pass": False}, frame_qa={"pass": True},
    )
    source = tmp_path / "telemetry" / "events.jsonl"
    rows = telemetry.read(source)
    assert len(rows) == 2 and source.is_file()
    summary = telemetry.summarize(rows)[0]
    assert summary["runs"] == 2
    assert summary["costUsd"] == 0.00018
    assert summary["avgLatencyMs"] == 100
    assert summary["status"] == "ok×2"
    assert summary["scriptQA"] == "1/2" and summary["frameQA"] == "2/2"
    assert "deepseek-flash" in telemetry.markdown(rows)
    assert "api_key" not in source.read_text().lower()


def test_script_test_writes_qa_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    result = script.write(
        "solo", "How should students verify AI claims?", ["Ada"],
        sheets=[{"quotes": []}], use_llm=False,
    )
    rows = telemetry.read()
    assert result["scriptQA"]["pass"]
    assert rows[-1]["operation"] == "script"
    assert rows[-1]["scriptQA"]["pass"]
    assert rows[-1]["provider"] == "offline"


def test_benchmark_dry_run_reports_every_missing_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    for env in (
        "DEEPSEEK_API_KEY", "DEEPINFRA_API_KEY", "OPENROUTER_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FAL_KEY",
        "TOGETHER_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    report = benchmark.run(tmp_path / "report", allow_paid_images=False)
    assert len(report["rows"]) == 4 + len(image_models.models())
    assert all(row["status"].startswith("dry-run:") for row in report["rows"])
    assert (tmp_path / "report" / "report.json").is_file()
    assert (tmp_path / "report" / "telemetry.md").is_file()
    assert all("KEY" in row["metadata"].get("missing", "") for row in report["rows"])
    json.dumps(report)  # public report contains JSON-safe aggregate fields only
