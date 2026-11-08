"""Provider guard: deny before the socket, record usage, keep prompts out of Postgres."""
from __future__ import annotations

import json
import threading
import uuid

import httpx
import pytest

from products.animind_core import image_models, llm_providers
from products.animind_core.spend import ledger
from products.animind_core.spend.guard import (
    SpendDenied,
    billable,
    frame_scope,
    open_frame,
    request_id,
    user_hash,
)
from products.animind_core.spend.limits import read_limits

URL = "postgresql://animind_test@127.0.0.1:5432/animind_spend_test"
PROMPT = "PROMPT-SHOULD-NOT-LEAK-99"


def _postgres():
    try:
        import psycopg
        with psycopg.connect(URL) as conn:
            conn.execute("SELECT 1")
    except Exception:
        pytest.skip("local animind_spend_test postgres is not reachable")


@pytest.fixture()
def schema():
    _postgres()
    import psycopg
    name = f"t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL) as conn:
        conn.execute(f"CREATE SCHEMA {name}")
        conn.commit()
    yield name
    with psycopg.connect(URL) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        conn.commit()


def _limits(monkeypatch, schema_name: str, **overrides: str) -> None:
    values = {
        "ANIMIND_GENERATION": "1",
        "ANIMIND_SPEND_DATABASE_URL": URL,
        "ANIMIND_SPEND_SCHEMA": schema_name,
        "ANIMIND_SPEND_BUDGET_CENTS": "100",
        "ANIMIND_SPEND_USER_CENTS": "100",
        "ANIMIND_SPEND_TIMEOUT_MS": "5000",
        "ANIMIND_SPEND_MAX_RETRIES": "1",
        "ANIMIND_SPEND_RATE_PER_MIN": "30",
        "ANIMIND_SPEND_CONCURRENCY": "2",
        "ANIMIND_SPEND_MAX_INPUT_TOKENS": "2000",
        "ANIMIND_SPEND_MAX_OUTPUT_TOKENS": "2000",
        "ANIMIND_SPEND_MAX_TOTAL_TOKENS": "4000",
        "ANIMIND_SPEND_MAX_IMAGES": "4",
        "ANIMIND_SPEND_MAX_AUDIO_SEC": "180",
        "ANIMIND_SPEND_MAX_VIDEO_SEC": "180",
        "ANIMIND_SPEND_ORIGIN": "test",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ANIMIND_PRICE_TABLE_JSON", raising=False)


def _frame(user: str = "user_abc", role: str = "user"):
    return open_frame(user_ref=user_hash(user), role=role, request=request_id(), origin="test")


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _deepseek_payload():
    return {
        "choices": [{"message": {"content": json.dumps({"lines": [{"i": 0, "text": "Hook."}]})}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "prompt_cache_hit_tokens": 200},
    }


def test_code_defaults_are_the_models_the_process_starts_with():
    assert llm_providers.DEEPSEEK_MODEL == "deepseek-flash"
    assert llm_providers.GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert image_models.DEFAULTS["together-qwen-image"].model == "Qwen/Qwen-Image"
    assert image_models.DEFAULTS["nano-banana"].model == "gemini-2.5-flash-image"


def test_generation_flag_without_a_request_frame_still_denies(monkeypatch):
    monkeypatch.setenv("ANIMIND_GENERATION", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(1))
    with pytest.raises(SpendDenied) as caught:
        llm_providers.DeepSeekProvider().complete(PROMPT)
    assert caught.value.block == "unavailable"
    assert calls == []
    monkeypatch.delenv("ANIMIND_GENERATION", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(args))
    with frame_scope(_frame()):
        with pytest.raises(SpendDenied) as caught:
            llm_providers.DeepSeekProvider().complete(PROMPT)
    assert caught.value.block == "disabled"
    assert calls == []


def test_missing_ledger_makes_zero_provider_calls(monkeypatch):
    monkeypatch.setenv("ANIMIND_GENERATION", "1")
    monkeypatch.setenv("ANIMIND_SPEND_DATABASE_URL", "postgresql://animind_test@127.0.0.1:1/none")
    monkeypatch.setenv("ANIMIND_SPEND_BUDGET_CENTS", "100")
    monkeypatch.setenv("ANIMIND_SPEND_USER_CENTS", "100")
    monkeypatch.setenv("ANIMIND_SPEND_TIMEOUT_MS", "5000")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_RETRIES", "0")
    monkeypatch.setenv("ANIMIND_SPEND_RATE_PER_MIN", "10")
    monkeypatch.setenv("ANIMIND_SPEND_CONCURRENCY", "1")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_INPUT_TOKENS", "1000")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_TOTAL_TOKENS", "2000")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_IMAGES", "1")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_AUDIO_SEC", "30")
    monkeypatch.setenv("ANIMIND_SPEND_MAX_VIDEO_SEC", "30")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(1))
    with frame_scope(_frame()):
        with pytest.raises(SpendDenied) as caught:
            llm_providers.DeepSeekProvider().complete(PROMPT)
    assert caught.value.block == "ledger"
    assert calls == []


def test_budget_and_unpriced_image_make_zero_provider_calls(schema, monkeypatch, tmp_path):
    _limits(monkeypatch, schema, ANIMIND_SPEND_BUDGET_CENTS="0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "1")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append("deepseek"))
    monkeypatch.setattr(image_models.httpx, "post", lambda *args, **kwargs: calls.append("image"))
    with frame_scope(_frame()):
        with pytest.raises(SpendDenied) as text:
            llm_providers.DeepSeekProvider().complete(PROMPT)
        with pytest.raises(SpendDenied) as image:
            image_models.provider("together-qwen-image").generate("bust", destination=tmp_path / "x.png")
    assert text.value.block == "budget"
    assert image.value.block == "price_unavailable"
    assert calls == []


def test_denied_writer_does_not_fall_through_to_the_next_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("ANIMIND_GENERATION", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    seen = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: seen.append("http"))

    def gemini(prompt):
        seen.append("gemini")
        return [{"i": 0, "text": "no"}]

    with frame_scope(_frame()):
        result, failures = llm_providers.generate(PROMPT, tmp_path, gemini)
    assert result is None
    assert seen == []
    assert any(item.startswith("deepseek: blocked:") for item in failures)


def test_success_records_cache_and_does_not_invent_a_reported_zero(schema, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return _Response(_deepseek_payload())

    monkeypatch.setattr(llm_providers.httpx, "post", post)
    frame = _frame()
    frame.job_id = "jobabc123"
    with frame_scope(frame):
        result = llm_providers.DeepSeekProvider().complete(PROMPT)
    assert len(calls) == 1
    assert result.input_tokens == 1000
    call = frame.events[0]
    assert call["usage"]["cachedTokens"] == 200
    assert call["usage"]["inputTokens"] == 1000
    assert call["usage"]["outputTokens"] == 500
    assert call["cost"]["estimatedMicro"] == 842
    assert call["cost"]["reportedMicro"] is None
    assert call["cost"]["status"] == "estimated"
    assert call["cost"]["priceVersion"] == "deepseek-peak-2026-11-07"
    stored = ledger.dump_text()
    assert PROMPT not in stored
    assert "not-a-real-key" not in stored
    assert "user_abc" not in stored


def test_retry_over_the_cap_does_not_call_again(schema, monkeypatch):
    _limits(monkeypatch, schema, ANIMIND_SPEND_MAX_RETRIES="0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(1) or _Response(_deepseek_payload()))
    with frame_scope(_frame()):
        llm_providers.DeepSeekProvider().complete(PROMPT)
        with pytest.raises(SpendDenied) as caught:
            llm_providers.DeepSeekProvider().complete(PROMPT)
    assert caught.value.block == "over_cap"
    assert calls == [1]


def test_timeout_keeps_cost_unknown_and_holds_the_ceiling(schema, monkeypatch):
    _limits(monkeypatch, schema)

    def post(url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(llm_providers.httpx, "post", post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    frame = _frame()
    with frame_scope(frame):
        with pytest.raises(httpx.TimeoutException):
            llm_providers.DeepSeekProvider().complete(PROMPT)
    call = frame.events[0]
    assert call["status"] == "uncertain"
    assert call["cost"]["estimatedMicro"] is None
    assert call["cost"]["reportedMicro"] is None
    assert call["cost"]["status"] == "unknown"
    assert ledger.holds()["global"]["settled"] >= 1


def test_global_cap_includes_admin(schema, monkeypatch):
    _limits(monkeypatch, schema, ANIMIND_SPEND_BUDGET_CENTS="1", ANIMIND_SPEND_USER_CENTS="100")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(1) or _Response(_deepseek_payload()))
    with frame_scope(_frame("user_one")):
        llm_providers.DeepSeekProvider().complete(PROMPT + " one")
    with frame_scope(_frame("admin_one", role="admin")):
        with pytest.raises(SpendDenied) as caught:
            llm_providers.DeepSeekProvider().complete(PROMPT + " two")
    assert caught.value.block == "budget"
    assert calls == [1]


def test_gemini_text_and_disabled_image_adapter_make_zero_provider_calls(schema, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "not-a-real-key")
    calls = []

    def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("provider was called")

    monkeypatch.setattr("production.cost.google_guardrail.guarded_generate_content", blocked)
    monkeypatch.setattr("production.cost.google_guardrail.make_guarded_gemini_client", lambda key: object())
    from products.animind_core import script

    with frame_scope(_frame()):
        with pytest.raises(SpendDenied) as text:
            script._gemini_call("do not send this prompt")
        with pytest.raises(SpendDenied) as image:
            image_models.provider("qwen-image").generate("do not spend")
    assert text.value.block == "price_unavailable"
    assert image.value.block == "unavailable"
    assert calls == []


def test_stream_flag_never_opens_the_socket(schema, monkeypatch):
    _limits(monkeypatch, schema)
    entered = []
    with frame_scope(_frame()):
        with pytest.raises(SpendDenied) as caught:
            with billable(provider="deepseek", model="deepseek-flash", operation="text", prompt=PROMPT, stream=True):
                entered.append(True)
    assert caught.value.block == "stream"
    assert entered == []


def test_rate_limit_is_atomic(schema, monkeypatch):
    _limits(monkeypatch, schema, ANIMIND_SPEND_RATE_PER_MIN="1", ANIMIND_SPEND_CONCURRENCY="4")
    limits = read_limits()
    assert limits is not None
    assert ledger.reserve(_entry("spd_rate_a", "user-a"), 1, limits) == "ok"
    assert ledger.reserve(_entry("spd_rate_b", "user-a"), 1, limits) == "rate"


def test_concurrency_limit_is_atomic(schema, monkeypatch):
    _limits(monkeypatch, schema, ANIMIND_SPEND_RATE_PER_MIN="10", ANIMIND_SPEND_CONCURRENCY="1", ANIMIND_SPEND_BUDGET_CENTS="1000")
    limits = read_limits()
    assert limits is not None
    results: list[str] = []
    barrier = threading.Barrier(2)

    def once(index: int) -> None:
        barrier.wait()
        results.append(ledger.reserve(_entry(f"spd_busy_{index}", "user-b"), 1, limits))

    threads = [threading.Thread(target=once, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["busy", "ok"]


def _entry(entry_id: str, user: str) -> dict:
    return {
        "id": entry_id, "request_id": "req_test", "attempt": 1, "job_ref": None,
        "user_id": user_hash(user), "operation": "text", "provider": "deepseek",
        "model": "deepseek-flash", "status": "reserved", "block": None, "reason": None,
        "origin": "test", "idempotency_key": None, "currency": "USD", "price_version": "test",
    }


def test_plan_render_and_job_poll_do_not_double_charge(schema, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    calls = []
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(1))
    from fastapi.testclient import TestClient

    from products.animind_core.app import app
    from products.animind_core.spend.guard import frame_scope as scope

    client = TestClient(app)
    headers = {"Authorization": "Bearer test-key", "X-Animind-User": "user_abc"}
    plan = client.post("/v1/plan", headers=headers, json={"prompt": "Einstein explica a luz"})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["usage"]["schema"] == "animind.usage.v1"
    assert body["usage"]["calls"] == []
    assert body["usage"]["totals"]["estimatedMicro"] is None
    assert body["usage"]["totals"]["reportedMicro"] is None
    assert body["planner"]["billable"] is False
    assert body["characters"]
    refused = client.post("/v1/plan", headers={**headers, "X-Animind-User": "diogo@example.com"}, json={"prompt": "oi"})
    assert refused.status_code == 400
    assert "diogo@example.com" not in refused.text

    frame = _frame()
    frame.job_id = "jobpoll1234"
    monkeypatch.setattr(llm_providers.httpx, "post", lambda *args, **kwargs: calls.append(1) or _Response(_deepseek_payload()))
    with scope(frame):
        llm_providers.DeepSeekProvider().complete(PROMPT)
    before = ledger.count_entries()
    polled = client.get("/v1/jobs/jobpoll1234", headers=headers)
    assert polled.status_code == 200, polled.text
    assert polled.json()["usage"]["calls"]
    assert ledger.count_entries() == before
    assert calls  # the priced call happened once; the poll did not add one
    assert PROMPT not in ledger.dump_text()


def test_idempotency_is_user_plus_body_and_stores_no_prompt(schema, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    from fastapi.testclient import TestClient

    from products.animind_core import render
    from products.animind_core.app import app

    calls = []

    def submit(body, base):
        calls.append(body)
        return {"id": "job_once", "status": "queued", "progress": 0.0}

    monkeypatch.setattr(render, "submit", submit)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer test-key",
        "X-Animind-User": "user_abc",
        "Idempotency-Key": "same-key-01",
    }
    payload = {"characters": [{"id": "c1"}], "template": "solo", "scenario": "studio", "topic": PROMPT}
    first = client.post("/v1/render", headers=headers, json=payload)
    second = client.post("/v1/render", headers=headers, json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == "job_once"
    assert len(calls) == 1
    changed = dict(payload, topic="other topic")
    conflict = client.post("/v1/render", headers=headers, json=changed)
    assert conflict.status_code == 409
    assert len(calls) == 1
    assert PROMPT not in ledger.dump_text()
