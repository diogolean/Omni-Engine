# ADR 0007 — Google Drive is the library source on the VM

## Status

Accepted

## Context

The channel libraries Diogo keeps on the PC live under `G:\My Drive\...\outputs\<channel>\` (`library/post_*.json`, images, and clips). The Oracle VM does not mount that drive. Publishers were reading the same trees from Backblaze B2, which spends Class B and Class C transactions on listings and downloads.

Post Planner (the WordPress orchestrator) cannot fetch a public Google Drive URL. It still needs an HTTPS URL on B2. The publish ledger stays on B2 as well.

Deploy must not start the Pinterest scheduler. A Drive client must not gain write access, and it must not delete anything in Drive or in B2.

## Decision

Publishers read content from Google Drive first.

- `agents/media/drive_source.py` lists a prefix, returns a size, and downloads one file. The service account uses the `drive.readonly` scope. Every list and download sets `supportsAllDrives` so shared folders and Shared Drives work.
- `GOOGLE_DRIVE_SA_JSON` points at the key file under the mounted `credentials/` directory. That file stays git-ignored. The process never logs the JSON.
- Each channel has a root folder id: `DRIVE_LIBRARY_FOLDER_ID_<CHANNEL>`, or `drive_library_folder_id` / `drive_folder_id` / `DRIVE_LIBRARY_FOLDER_ID` in channel config. The folder may be `outputs/<channel>` (library and clips) or the `library` folder itself.
- `LIBRARY_SOURCE=drive|b2|local` forces one source. When it is unset and the service-account file exists, the order is Drive, then the local tree (the PC `G:` path still wins when the file is already on disk), then B2.
- Listings are cached with a TTL. File bytes are cached on the outputs volume. An image or video is downloaded when that one asset is published, not when the library is listed. `post_*.json` records are copied so the inventory can be built; images inside the listing are not.
- Post Planner keeps a B2 public URL. If the asset exists only on Drive, that single file is uploaded through the budgeted B2 client (Class A `put`) and left in the bucket. This change does not edit `agents/media/b2_budget.py`.
- `.github/scripts/remote-deploy.sh` is unchanged. Deploy still starts the engine only. The scheduler stays behind its Compose profile.

## Consequences

- The VM can publish Ancient Knowledge, Pinterest, and YouTube from the Drive libraries without a full B2 mirror.
- A PC run with the `G:` tree on disk does not need Drive credentials.
- Post Planner rows still contain B2 URLs. Drive links are not written into the sheet.
- Losing the service-account share (Viewer) makes that channel fall through to local, then B2, unless `LIBRARY_SOURCE=drive`.
- Drive quota errors back off and, when Drive is the only selected source, stop the run. They do not delete or retry forever.
