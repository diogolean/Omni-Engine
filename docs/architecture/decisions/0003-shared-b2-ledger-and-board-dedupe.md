# ADR 0003 — Shared B2 ledger and board dedupe

## Status

Accepted

## Context

Pinterest and the PC runner can both see the same B2 prefix. Publishing from memory on one machine republishes pins the other machine already created, including onto the same board.

## Decision

The publish record lives with the outputs that both runners can see, backed by the B2 object identity:

- `outputs/pinterest/published_pins.json` (`omni/pinterest_drip.PublishedPins`) stores account, B2 object key, article, and pin id.
- `publish_ledger.json` beside it (`omni/publisher.FileIdempotencyStore`) remembers a successful publish key so a replay does not post twice.
- Before a pin is created, keys already in the ledger for that account are removed from the pending list.
- Each branding has its own board id (`PINTEREST_BOARD_ID_<BRANDING>` via `omni/accounts.py`). Dedupe is per account and per object, so two brands can use one bucket without sharing a board.

The VM scheduler and a PC run of the same drip consult the same ledger. They do not each keep a private “already pinned” set.

## Consequences

- Restarting the scheduler does not recreate pins for objects already recorded.
- A filename that matches no article is skipped and logged. It is not given a title taken from the filename.
- Losing the ledger file would allow duplicates. The file stays on the mounted outputs volume, not inside the image.
