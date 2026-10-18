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
install -m 600 /dev/stdin /opt/omni-engine/credentials/google-drive-sa.json
```

Paste the JSON, then `Ctrl-D`. Do not commit the file. `credentials/*.json` is git-ignored. Do not print the file, and do not paste it into chat or logs.

5. Copy the service account email (`client_email` in the JSON). You will share folders with that address. Do not share the private key.

## Share each library as Viewer

The PC tree is `G:\My Drive\...\outputs\<channel>\`. Share that channel folder, or share only `outputs/<channel>/library` when the channel has no clips to publish.

1. In Google Drive, open the folder.
2. Share → add the service account email → **Viewer**.
3. For a Shared Drive, add the service account as a Viewer member of the drive (or share the folder with it). The client sends `supportsAllDrives` and `includeItemsFromAllDrives` on every call.
4. Copy the folder id from the URL: `https://drive.google.com/drive/folders/<folder id>`.

Viewer is enough. Do not grant Editor. The scope in code is `https://www.googleapis.com/auth/drive.readonly`.

## Environment

In `/opt/omni-engine/.env` (mode 600, not in git):

```bash
GOOGLE_DRIVE_SA_JSON=/opt/omni-engine/credentials/google-drive-sa.json
DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE=<folder id>
# DRIVE_LIBRARY_FOLDER_ID_AIWAKE=<folder id>
# LIBRARY_SOURCE=drive
```

`DRIVE_LIBRARY_FOLDER_ID_<CHANNEL>` uses the channel id in upper case, with non-alphanumerics turned into underscores (`ancient_knowledge` → `ANCIENT_KNOWLEDGE`).

The same id can live in channel config instead of the environment:

- `drive_library_folder_id` on the branding in `config/brandings.yaml`
- `drive_folder_id` on the account in `config/pinterest_accounts.yaml`
- `DRIVE_LIBRARY_FOLDER_ID` in `channels_config/<channel>/page_config.py`

The environment variable wins over those files.

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

- Do not commit the service-account JSON or print it.
- Do not widen the scope past `drive.readonly`.
- Do not point a publisher at a script that deletes Drive or B2 objects.
- Do not start the scheduler as part of wiring Drive.
