# Google Drive libraries on the VM

Publishers on the Oracle VM read channel libraries from Google Drive before Backblaze B2. The client is a service account with the **drive.readonly** scope. It lists folders and downloads one file at publish time. It does not delete Drive files, and it does not delete B2 objects.

See `docs/architecture/decisions/0007-google-drive-library-source.md`.

Deploy does not start the Pinterest scheduler. Nothing in this setup should run `docker compose --profile scheduler up`.

## Service account

1. In Google Cloud Console, open the project that should own the account and enable the **Google Drive API**.
2. IAM & Admin → Service Accounts → Create service account. No extra IAM roles are required for Drive file access.
3. Open the account → Keys → Add key → JSON. Download the key once.
4. On the VM, put the file in the credentials directory Compose already mounts:

```bash
install -m 600 /dev/stdin /opt/omni-engine/credentials/google_drive_sa.json
```

Paste the JSON, then `Ctrl-D`. Do not commit the file. `credentials/*.json` is git-ignored. Do not print the file, and do not paste it into chat or logs.

5. Copy the service account email (`client_email` in the JSON). You will share folders with that address. Do not share the private key.

## Share the outputs folder as Viewer

The PC tree and the Drive tree are the same shape: `outputs/<channel>/...` (`ancient_knowledge`, `aiwake`, `master_mei`, `anna_protocol`, `wonder_feed`, `endless_summer_paradise`, and the others). Share that one `outputs` folder. It lives in My Drive and is shared with the service account. It is not a Shared Drive.

`outputs/<channel>/library/` holds `post_*.json` only. Images referenced by those records live beside the library (usually `outputs/<channel>/assets/`, or another subfolder). Paths in the JSON are resolved relative to the outputs root, the same way `OUTPUT_PATH` resolves them locally.

1. In Google Drive, open the `outputs` folder.
2. Share → add the service account email → **Viewer**.
3. Copy the folder id from the URL: `https://drive.google.com/drive/folders/<folder id>`.

The verified outputs folder id is `1Fwc2YcdX-iMrmxqpHrUsmd6OtPe-4wU3`. Put that value in `.env`. Do not commit `.env`.

Viewer is enough. Do not grant Editor. A service account has no Drive storage quota, so the client must stay read-only. The scope in code is `https://www.googleapis.com/auth/drive.readonly`. The client still sends `supportsAllDrives` and `includeItemsFromAllDrives`, which is what a My Drive share needs and what a Shared Drive share would need.

A per-channel folder is optional. Share `outputs/<channel>` (or only `outputs/<channel>/library` when that channel has nothing beside the library) and set the override below.

## Environment

In `/opt/omni-engine/.env` (mode 600, not in git):

```bash
GOOGLE_DRIVE_SA_JSON=/opt/omni-engine/credentials/google_drive_sa.json
DRIVE_OUTPUTS_FOLDER_ID=1Fwc2YcdX-iMrmxqpHrUsmd6OtPe-4wU3
# Optional. Overrides the outputs root for one channel.
# DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE=<channel folder id>
# LIBRARY_SOURCE=drive
```

`DRIVE_OUTPUTS_FOLDER_ID` is the shared outputs folder. Object keys are paths under it (`ancient_knowledge/library/post_….json`, `ancient_knowledge/assets/….jpg`), matching `OUTPUT_PATH / key` on disk. A listing walks only the requested prefix, so a library sync does not list every channel.

`DRIVE_LIBRARY_FOLDER_ID_<CHANNEL>` is an optional override. The channel id is upper case, with non-alphanumerics turned into underscores (`ancient_knowledge` → `ANCIENT_KNOWLEDGE`). That folder is the channel tree, not the outputs root.

The same per-channel id can live in channel config instead of the environment:

- `drive_library_folder_id` on the branding in `config/brandings.yaml`
- `drive_folder_id` on the account in `config/pinterest_accounts.yaml`
- `DRIVE_LIBRARY_FOLDER_ID` in `channels_config/<channel>/page_config.py`

The per-channel environment variable wins over those files. Either one wins over `DRIVE_OUTPUTS_FOLDER_ID`.

`LIBRARY_SOURCE` is optional:

| Value | Behavior |
|---|---|
| unset, service-account file present | Drive, then local files (including the PC `G:` path), then B2 |
| unset, no service-account file | Local files, then B2 |
| `drive`, `local`, or `b2` | That source only |

On the PC, a file that is already under `G:\My Drive\...\outputs` is used as-is. Leave `GOOGLE_DRIVE_SA_JSON` unset there unless you want Drive to fill gaps.

Optional: `DRIVE_LIST_CACHE_TTL_SECONDS` (default 21600). Listings and downloaded files are stored under the outputs volume at `outputs/drive/cache/`.

## Reload the engine

Pick up the new env without starting the scheduler:

```bash
cd /opt/omni-engine
docker compose up -d engine
```

Confirm the scheduler is still stopped:

```bash
docker compose ps
```

`scheduler` should not be running. Start it later only with `SCHEDULER_ENABLED=1` or an explicit `docker compose --profile scheduler up -d scheduler`.

## Post Planner

Post Planner cannot read a Drive URL. The MEDIA URL column stays a B2 public URL.

If the mp4 or image exists only on Drive, the publisher downloads that one file and uploads it through the budgeted B2 client (one Class A put). Objects already in B2 are left in place. Nothing is deleted.

## What not to do

- Do not commit the service-account JSON or print it. Do not print the service account email from the key file.
- Do not widen the scope past `drive.readonly`, and do not upload to Drive. The service account has no storage quota.
- Do not point a publisher at a script that deletes Drive or B2 objects.
- Do not start the scheduler as part of wiring Drive.
