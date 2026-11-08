"""Postgres ledger. Reservations are atomic. Nothing here is process-local."""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

_SCHEMA = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_READY: set[str] = set()
_LOCK = threading.Lock()


class LedgerUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("ledger")


def database_url() -> str:
    return (os.getenv("ANIMIND_SPEND_DATABASE_URL") or "").strip()


def schema_name() -> str:
    raw = (os.getenv("ANIMIND_SPEND_SCHEMA") or "public").strip()
    if not _SCHEMA.match(raw):
        raise LedgerUnavailable()
    return raw


def configured() -> bool:
    return bool(database_url())


def _connect():
    try:
        import psycopg
    except ImportError as exc:
        raise LedgerUnavailable() from exc
    try:
        conn = psycopg.connect(database_url(), autocommit=False)
        schema = schema_name()
        if schema != "public":
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(f"SET search_path TO {schema}")
        return conn
    except LedgerUnavailable:
        raise
    except Exception as exc:
        raise LedgerUnavailable() from exc


def ensure() -> None:
    schema = schema_name()
    with _LOCK:
        if schema in _READY:
            return
        try:
            with _connect() as conn:
                _bootstrap(conn)
                conn.commit()
        except LedgerUnavailable:
            raise
        except Exception as exc:
            raise LedgerUnavailable() from exc
        _READY.add(schema)


def ping() -> bool:
    if not configured():
        return False
    try:
        ensure()
        with _connect() as conn:
            conn.execute("SELECT 1")
            conn.rollback()
        return True
    except LedgerUnavailable:
        return False


def _bootstrap(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animind_core_spend_entries (
          id text PRIMARY KEY,
          request_id text NOT NULL,
          attempt integer NOT NULL,
          job_ref text,
          user_id text NOT NULL,
          operation text NOT NULL,
          provider text NOT NULL,
          model text,
          status text NOT NULL,
          block text,
          reason text,
          latency_ms integer,
          input_tokens integer,
          output_tokens integer,
          cached_tokens integer,
          thinking_tokens integer,
          image_count integer,
          audio_seconds double precision,
          video_seconds double precision,
          estimated_micro bigint,
          reported_micro bigint,
          reserved_cents bigint,
          currency text,
          price_version text,
          origin text NOT NULL,
          origin_ref text,
          idempotency_key text,
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS animind_core_spend_entries_job ON animind_core_spend_entries (job_ref)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animind_core_spend_holds (
          scope text PRIMARY KEY,
          currency text NOT NULL,
          limit_cents bigint NOT NULL,
          reserved_cents bigint NOT NULL,
          settled_cents bigint NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animind_core_spend_rates (
          user_id text PRIMARY KEY,
          window_start timestamptz NOT NULL,
          count integer NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animind_core_spend_inflight (
          id integer PRIMARY KEY,
          slots integer NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO animind_core_spend_inflight (id, slots) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animind_core_spend_replay (
          user_id text NOT NULL,
          idempotency_key text NOT NULL,
          body_hash text NOT NULL,
          resource_id text,
          status integer NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (user_id, idempotency_key)
        )
        """
    )
    conn.execute(
        "ALTER TABLE animind_core_spend_entries ADD COLUMN IF NOT EXISTS origin_ref text"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS animind_core_spend_slots (
          scope text PRIMARY KEY,
          slots integer NOT NULL
        )
        """
    )
    conn.execute("DROP FUNCTION IF EXISTS animind_core_spend_reserve(bigint, bigint, bigint, text, text, integer, integer, timestamptz)")
    conn.execute(_RESERVE_SQL)


_RESERVE_SQL = r"""
CREATE OR REPLACE FUNCTION animind_core_spend_reserve(
  p_cents bigint, p_global bigint, p_user bigint, p_user_scope text, p_user_id text,
  p_origin_scope text, p_origin_id text, p_rate integer, p_concurrency integer,
  p_now timestamptz, p_request text, p_operation text
) RETURNS text LANGUAGE plpgsql AS $fn$
DECLARE
  v_user_count integer;
  v_user_window timestamptz;
  v_origin_count integer;
  v_origin_window timestamptz;
  v_global integer;
  v_user_slots integer;
  v_origin_slots integer;
  v_global_used bigint;
  v_user_used bigint;
  v_first text;
  v_second text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext(p_request || ':' || p_user_id || ':' || p_operation));
  INSERT INTO animind_core_spend_rates (user_id, window_start, count)
  VALUES (p_user_id, p_now, 0)
  ON CONFLICT (user_id) DO NOTHING;
  INSERT INTO animind_core_spend_rates (user_id, window_start, count)
  VALUES (p_origin_id, p_now, 0)
  ON CONFLICT (user_id) DO NOTHING;
  IF p_user_id <= p_origin_id THEN
    v_first := p_user_id;
    v_second := p_origin_id;
  ELSE
    v_first := p_origin_id;
    v_second := p_user_id;
  END IF;
  PERFORM 1 FROM animind_core_spend_rates WHERE user_id = v_first FOR UPDATE;
  IF v_second <> v_first THEN
    PERFORM 1 FROM animind_core_spend_rates WHERE user_id = v_second FOR UPDATE;
  END IF;
  IF EXISTS (
    SELECT 1 FROM animind_core_spend_entries
    WHERE request_id = p_request AND user_id = p_user_id AND operation = p_operation
      AND status IN ('reserved', 'succeeded', 'not_charged')
  ) THEN
    RETURN 'replay';
  END IF;
  SELECT count, window_start INTO v_user_count, v_user_window
  FROM animind_core_spend_rates WHERE user_id = p_user_id;
  SELECT count, window_start INTO v_origin_count, v_origin_window
  FROM animind_core_spend_rates WHERE user_id = p_origin_id;
  IF v_user_window <= p_now - interval '60 seconds' THEN
    v_user_count := 0;
    v_user_window := p_now;
  END IF;
  IF v_origin_window <= p_now - interval '60 seconds' THEN
    v_origin_count := 0;
    v_origin_window := p_now;
  END IF;
  IF v_user_count >= p_rate OR v_origin_count >= p_rate THEN
    RETURN 'rate';
  END IF;
  INSERT INTO animind_core_spend_holds (scope, currency, limit_cents, reserved_cents, settled_cents)
  VALUES ('global', 'USD', p_global, 0, 0)
  ON CONFLICT (scope) DO UPDATE SET limit_cents = EXCLUDED.limit_cents;
  INSERT INTO animind_core_spend_holds (scope, currency, limit_cents, reserved_cents, settled_cents)
  VALUES (p_user_scope, 'USD', p_user, 0, 0)
  ON CONFLICT (scope) DO UPDATE SET limit_cents = EXCLUDED.limit_cents;
  INSERT INTO animind_core_spend_slots (scope, slots) VALUES ('global', 0) ON CONFLICT (scope) DO NOTHING;
  INSERT INTO animind_core_spend_slots (scope, slots) VALUES (p_user_scope, 0) ON CONFLICT (scope) DO NOTHING;
  INSERT INTO animind_core_spend_slots (scope, slots) VALUES (p_origin_scope, 0) ON CONFLICT (scope) DO NOTHING;
  PERFORM 1 FROM animind_core_spend_holds WHERE scope = 'global' FOR UPDATE;
  PERFORM 1 FROM animind_core_spend_holds WHERE scope = p_user_scope FOR UPDATE;
  PERFORM 1 FROM animind_core_spend_inflight WHERE id = 1 FOR UPDATE;
  IF p_user_scope <= p_origin_scope THEN
    PERFORM 1 FROM animind_core_spend_slots WHERE scope = p_user_scope FOR UPDATE;
    IF p_origin_scope <> p_user_scope THEN
      PERFORM 1 FROM animind_core_spend_slots WHERE scope = p_origin_scope FOR UPDATE;
    END IF;
  ELSE
    PERFORM 1 FROM animind_core_spend_slots WHERE scope = p_origin_scope FOR UPDATE;
    PERFORM 1 FROM animind_core_spend_slots WHERE scope = p_user_scope FOR UPDATE;
  END IF;
  PERFORM 1 FROM animind_core_spend_slots WHERE scope = 'global' FOR UPDATE;
  SELECT slots INTO v_global FROM animind_core_spend_inflight WHERE id = 1;
  SELECT slots INTO v_user_slots FROM animind_core_spend_slots WHERE scope = p_user_scope;
  SELECT slots INTO v_origin_slots FROM animind_core_spend_slots WHERE scope = p_origin_scope;
  IF v_global + 1 > p_concurrency OR v_user_slots + 1 > p_concurrency
     OR v_origin_slots + 1 > p_concurrency THEN
    RETURN 'busy';
  END IF;
  SELECT settled_cents + reserved_cents INTO v_global_used FROM animind_core_spend_holds WHERE scope = 'global';
  SELECT settled_cents + reserved_cents INTO v_user_used FROM animind_core_spend_holds WHERE scope = p_user_scope;
  IF v_global_used + p_cents > p_global OR v_user_used + p_cents > p_user THEN
    RETURN 'budget';
  END IF;
  UPDATE animind_core_spend_rates SET count = v_user_count + 1, window_start = v_user_window
  WHERE user_id = p_user_id;
  UPDATE animind_core_spend_rates SET count = v_origin_count + 1, window_start = v_origin_window
  WHERE user_id = p_origin_id;
  UPDATE animind_core_spend_holds SET reserved_cents = reserved_cents + p_cents WHERE scope = 'global';
  UPDATE animind_core_spend_holds SET reserved_cents = reserved_cents + p_cents WHERE scope = p_user_scope;
  UPDATE animind_core_spend_inflight SET slots = slots + 1 WHERE id = 1;
  UPDATE animind_core_spend_slots SET slots = slots + 1 WHERE scope IN (p_user_scope, p_origin_scope);
  RETURN 'ok';
END
$fn$
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    import hashlib
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def reserve(entry: dict[str, Any], cents: int, limits: Any) -> str:
    """Return ``ok``, ``replay``, or a block code. The entry is inserted only on ``ok``."""
    ensure()
    user_scope = f"user:{entry['user_id']}"
    origin_ref = str(entry.get("origin_ref") or _hash(str(entry.get("origin") or "unknown")))
    origin_scope = f"origin:{origin_ref}"
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT animind_core_spend_reserve(
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    cents, limits.budget_cents, limits.user_cents, user_scope, entry["user_id"],
                    origin_scope, origin_ref, limits.rate_per_min, limits.concurrency, _now(),
                    entry.get("request_id") or "", entry.get("operation") or "",
                ),
            ).fetchone()
            result = str(row[0] if row else "budget")
            if result == "replay":
                conn.rollback()
                return "replay"
            if result != "ok":
                conn.rollback()
                return result if result in {"rate", "busy", "budget"} else "budget"
            _insert(conn, entry | {"reserved_cents": cents, "status": "reserved", "origin_ref": origin_ref})
            conn.commit()
            return "ok"
    except LedgerUnavailable:
        raise
    except Exception as exc:
        raise LedgerUnavailable() from exc


def insert_status(entry: dict[str, Any]) -> None:
    """A blocked or unbilled row. It does not move a hold."""
    if not configured():
        return
    ensure()
    try:
        with _connect() as conn:
            _insert(conn, entry)
            conn.commit()
    except LedgerUnavailable:
        raise
    except Exception as exc:
        raise LedgerUnavailable() from exc


def _insert(conn, entry: dict[str, Any]) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO animind_core_spend_entries (
          id, request_id, attempt, job_ref, user_id, operation, provider, model, status, block, reason,
          latency_ms, input_tokens, output_tokens, cached_tokens, thinking_tokens, image_count,
          audio_seconds, video_seconds, estimated_micro, reported_micro, reserved_cents, currency,
          price_version, origin, origin_ref, idempotency_key, created_at, updated_at
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s
        )
        """,
        (
            entry["id"], entry["request_id"], entry["attempt"], entry.get("job_ref"), entry["user_id"],
            entry["operation"], entry["provider"], entry.get("model"), entry["status"], entry.get("block"),
            entry.get("reason"), entry.get("latency_ms"), entry.get("input_tokens"), entry.get("output_tokens"),
            entry.get("cached_tokens"), entry.get("thinking_tokens"), entry.get("image_count"),
            entry.get("audio_seconds"), entry.get("video_seconds"), entry.get("estimated_micro"),
            entry.get("reported_micro"), entry.get("reserved_cents"), entry.get("currency"),
            entry.get("price_version"), entry.get("origin") or "live", entry.get("origin_ref"),
            entry.get("idempotency_key"),
            now, now,
        ),
    )


def settle(entry_id: str, status: str, patch: dict[str, Any]) -> None:
    ensure()
    keep = status in {"succeeded", "uncertain"}
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT reserved_cents, user_id, status, origin_ref FROM animind_core_spend_entries WHERE id = %s FOR UPDATE",
                (entry_id,),
            ).fetchone()
            if row and row[2] == "reserved":
                cents = int(row[0] or 0)
                scope = f"user:{row[1]}"
                origin_scope = f"origin:{row[3]}" if row[3] else None
                conn.execute(
                    "UPDATE animind_core_spend_inflight SET slots = GREATEST(slots - 1, 0) WHERE id = 1"
                )
                conn.execute(
                    "UPDATE animind_core_spend_slots SET slots = GREATEST(slots - 1, 0) WHERE scope = %s",
                    (scope,),
                )
                if origin_scope:
                    conn.execute(
                        "UPDATE animind_core_spend_slots SET slots = GREATEST(slots - 1, 0) WHERE scope = %s",
                        (origin_scope,),
                    )
                for held in ("global", scope):
                    conn.execute(
                        """
                        UPDATE animind_core_spend_holds SET
                          reserved_cents = GREATEST(reserved_cents - %s, 0),
                          settled_cents = settled_cents + %s
                        WHERE scope = %s
                        """,
                        (cents, cents if keep else 0, held),
                    )
            conn.execute(
                """
                UPDATE animind_core_spend_entries SET
                  status = %s,
                  block = COALESCE(%s, block),
                  reason = COALESCE(%s, reason),
                  latency_ms = COALESCE(%s, latency_ms),
                  input_tokens = %s,
                  output_tokens = %s,
                  cached_tokens = %s,
                  thinking_tokens = %s,
                  image_count = %s,
                  audio_seconds = %s,
                  video_seconds = %s,
                  estimated_micro = %s,
                  reported_micro = %s,
                  model = COALESCE(%s, model),
                  updated_at = %s
                WHERE id = %s
                """,
                (
                    status, patch.get("block"), patch.get("reason"), patch.get("latency_ms"),
                    patch.get("input_tokens"), patch.get("output_tokens"), patch.get("cached_tokens"),
                    patch.get("thinking_tokens"), patch.get("image_count"), patch.get("audio_seconds"),
                    patch.get("video_seconds"), patch.get("estimated_micro"), patch.get("reported_micro"),
                    patch.get("model"), _now(), entry_id,
                ),
            )
            conn.commit()
    except LedgerUnavailable:
        raise
    except Exception as exc:
        raise LedgerUnavailable() from exc


def rows_for_job(job_ref: str) -> list[dict[str, Any]]:
    if not job_ref or not configured():
        return []
    try:
        ensure()
        with _connect() as conn:
            cur = conn.execute(
                """
                SELECT id, request_id, attempt, job_ref, user_id, operation, provider, model, status, block,
                       reason, latency_ms, input_tokens, output_tokens, cached_tokens, thinking_tokens,
                       image_count, audio_seconds, video_seconds, estimated_micro, reported_micro,
                       reserved_cents, price_version
                FROM animind_core_spend_entries
                WHERE job_ref = %s
                ORDER BY created_at ASC
                """,
                (job_ref,),
            )
            columns = [desc.name for desc in cur.description]
            found = [dict(zip(columns, row)) for row in cur.fetchall()]
            conn.rollback()
            return found
    except LedgerUnavailable:
        return []


def count_entries() -> int:
    ensure()
    with _connect() as conn:
        row = conn.execute("SELECT count(*) FROM animind_core_spend_entries").fetchone()
        conn.rollback()
        return int(row[0])


def dump_text() -> str:
    """Every stored column, for tests that forbid prompts and secrets."""
    ensure()
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM animind_core_spend_entries")
        text = "\n".join(str(row) for row in cur.fetchall())
        replay = conn.execute("SELECT user_id, idempotency_key, body_hash, resource_id, status FROM animind_core_spend_replay")
        text += "\n" + "\n".join(str(row) for row in replay.fetchall())
        conn.rollback()
        return text


def holds() -> dict[str, dict[str, int]]:
    ensure()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scope, reserved_cents, settled_cents, limit_cents FROM animind_core_spend_holds"
        ).fetchall()
        conn.rollback()
    return {row[0]: {"reserved": int(row[1]), "settled": int(row[2]), "limit": int(row[3])} for row in rows}


def find_replay(user_id: str, key: str) -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT body_hash, resource_id, status, created_at
            FROM animind_core_spend_replay
            WHERE user_id = %s AND idempotency_key = %s
            """,
            (user_id, key),
        ).fetchone()
        conn.rollback()
    if not row:
        return None
    created = row[3]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (_now() - created).total_seconds()
    if age > 600:
        return None
    return {"body_hash": row[0], "resource_id": row[1], "status": int(row[2])}


def save_replay(user_id: str, key: str, body_hash: str, resource_id: str | None, status: int) -> None:
    ensure()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO animind_core_spend_replay (user_id, idempotency_key, body_hash, resource_id, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, idempotency_key) DO UPDATE
            SET body_hash = EXCLUDED.body_hash, resource_id = EXCLUDED.resource_id,
                status = EXCLUDED.status, created_at = EXCLUDED.created_at
            """,
            (user_id, key, body_hash, resource_id, status, _now()),
        )
        conn.commit()
