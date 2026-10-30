"""Animind core: a small versioned HTTP API over the Aiwake factory and Rigly.

Imports only ``products.aiwake.animator.factory``, ``animation`` and the
``production.cost`` guardrails. Never imports channels or the Animind app.
"""
API_VERSION = "v1"  # URL prefix; payloads follow contract v2 (lib/contract.ts)
