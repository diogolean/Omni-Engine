# ADR 0003 — Shared B2 ledger and board dedupe

## Status

Accepted

## Context

Pinterest and the PC runner can both see the same B2 prefix. Publishing from memory on one machine republishes pins the other machine already created, including onto the same board.

## Decision

The publish record lives with the outputs that both runners can see, backed by the B2 object identity:

- The live pin record is the B2 ledger `pinterest/ledger/<account>.jsonl` (`distribution/scheduling/ledger.py`). It stores account, source, and pin id.
- `$OUTPUT_PATH/_omni/publish_ledger.json` (`distribution.publishers.base.FileIdempotencyStore`, override `OMNI_LEDGER_PATH`) remembers a successful publish key so a replay does not post twice.
- Before a pin is created, keys already in the ledger for that account are removed from the pending list.
- Each branding has its own board id (`PINTEREST_BOARD_ID_<BRANDING>` via `accounts/tokens.py`). Dedupe is per account and per object, so two brands can use one bucket without sharing a board.

The VM scheduler and a PC run of the same drip consult the same ledger. They do not each keep a private “already pinned” set.

## Consequences

- Restarting the scheduler does not recreate pins for objects already recorded.
- A filename that matches no article is skipped and logged. It is not given a title taken from the filename.
- Losing the ledger file would allow duplicates. The file stays on the mounted outputs volume, not inside the image.
