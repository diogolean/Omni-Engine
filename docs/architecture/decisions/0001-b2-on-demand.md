# ADR 0001 — B2 is fetched on demand

## Status

Accepted

## Context

The factory used to copy media trees between the PC, the VM, and a bucket. Full sync duplicated bytes, drifted, and made it unclear which copy was safe to publish.

## Decision

Backblaze B2 is the store for media, the content library, and the publish ledger. A runner lists keys and downloads a single object when it is about to use it. Nothing in the bucket is deleted by the publish path, and the temp file on the VM is removed after the pin or upload is done.

`agents/media/b2_client.list_object_keys` and `download_object` are the read API. `B2VideoUploader.upload` is the write API.

## Consequences

- The VM disk does not need a mirror of the bucket.
- A job can resume on the other runner because the object is in B2, not on one machine.
- Listing a large prefix is the cost of finding the next unpublished image. That is cheaper than syncing the prefix.
