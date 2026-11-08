"""Spend guard for Animindly provider calls.

The HTTP API and the CLIs open a frame. Provider adapters call
:func:`products.animind_core.spend.guard.billable` before any network I/O.
Production reservations live in Postgres. There is no in-memory ledger.
"""
