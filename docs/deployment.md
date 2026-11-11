# Deployment

StackTriage is a single-user application with one SQLite database and one backend
instance. The Docker image serves both the API and the built React application.
Do not scale it across hosts or use an ephemeral filesystem for the database.

**Do not upload confidential logs.** Demonstrate with the synthetic fixtures only;
redaction is best-effort, and raw uploads may spill to host temporary storage.

## Local Docker deployment

Prerequisites: Docker with a running Linux-container daemon. Run these commands
from the repository root. The multi-stage Dockerfile runs `npm ci` and
`npm run build` in `frontend/`, then installs the Python `api` and `ml` extras.
No prebuilt frontend or generated model artifact is required.

```sh
docker build -t stacktriage:local .
docker volume create stacktriage-data
```

Set `STACKTRIAGE_API_KEY` in your shell to a randomly generated secret of at least
16 characters, preferably 32 or more. For example, in PowerShell, enter a secret
from a password manager without recording its value in shell history:

```powershell
$env:STACKTRIAGE_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'API key' -AsSecureString)).Password
```

```sh
docker run -d --name stacktriage --restart unless-stopped -p 127.0.0.1:8000:8000 --env STACKTRIAGE_API_KEY --mount source=stacktriage-data,target=/data stacktriage:local
curl http://127.0.0.1:8000/api/health
```

Open <http://127.0.0.1:8000/> and enter the same API key. The health endpoint and
static application are public; data endpoints require `Authorization: Bearer KEY`
or `X-API-Key: KEY`. The image defaults to production and refuses startup without
a key. Do not bake a key into the image or into a frontend `VITE_*` variable.

The local example binds only to loopback. For remote access, place a TLS reverse
proxy in front of the service, restrict access to intended users, and keep the key
secret. Do not expose authenticated requests over plain HTTP on the public internet.

The image runs as UID 10001 (`app`). A new Docker named volume inherits `/data`'s
ownership from the image. An existing volume or host bind mount must be writable
by that UID. Keep the entire `/data` directory, including SQLite WAL/SHM files;
removing and recreating a container with the same volume preserves its data.
`docker rm` does not delete a named volume; `docker volume rm` does.

Static review: the Dockerfile creates `/data`, assigns it to UID 10001 and switches
to `USER app` before startup. SQLite needs write access to the directory, not just
the `.db` file, to create WAL/SHM files. Mounted directory permissions override image
permissions; the image does not automatically repair an existing mount. No Docker
build or runtime check was performed as part of this documentation review.

## Environment variables

| Variable | Image default | Meaning |
|---|---|---|
| `STACKTRIAGE_ENV` | `production` | `production` requires a key; `development` permits unauthenticated local use. |
| `STACKTRIAGE_API_KEY` | unset; required | Shared API secret, at least 16 characters. |
| `STACKTRIAGE_DB` | `/data/stacktriage.db` | SQLite path; keep it inside the persistent mount. |
| `STACKTRIAGE_STATIC_DIR` | `/app/frontend/dist` | Built SPA directory. |
| `STACKTRIAGE_MAX_UPLOAD_MB` | `25` | Maximum raw upload size in MiB. |
| `STACKTRIAGE_MAX_EVENTS` | `200000` | Maximum parsed events per import. |
| `STACKTRIAGE_ALLOWED_HOSTS` | automatic | Comma-separated allowed Host names. With a key, the automatic setting does not restrict hosts; without a key it permits loopback/test hosts only. |
| `STACKTRIAGE_CORS_ORIGINS` | empty | Comma-separated browser origins allowed for direct cross-origin access. Not needed for the same-origin Docker UI. |

The Docker command listens on `0.0.0.0:8000`; a `PORT` environment variable alone
does not change the CLI port. The Render blueprint explicitly sets `PORT=8000`
to match it. The Docker health check probes `/api/health` every 30 seconds.

## Backup and restore

Use the application's SQLite online-backup command, not a copy of the live `.db`
file alone: committed data can still be in the WAL. Backups contain redacted logs,
triage state and notes; treat them as sensitive and keep encrypted off-host copies.
Choose a new filename for every backup (the command refuses to overwrite unless
`--force` is supplied).

The backup destination must differ from the source database, even with `--force`;
same-file aliases are rejected too. Backup staging uses a unique temporary directory.

```sh
# Safe while the server runs; verifies the resulting database.
docker exec stacktriage stacktriage backup /data/backups/stacktriage-20261110.db
docker cp stacktriage:/data/backups/stacktriage-20261110.db ./stacktriage-20261110.db
```

Restore only with the server stopped. Use the same application image/version as
the backup or a version known to support its schema. The restore command verifies
the backup and retains the previous database as `stacktriage.db.before-restore`.

Only one `.before-restore` safety copy is retained: a later restore replaces it.
Restore stages the verified input before writing that safety copy, so a prior
`.before-restore` file can itself be the restore input. Staging failure leaves the
existing WAL/SHM files untouched. Keep independent off-host backup generations.

```sh
docker stop stacktriage
docker cp ./stacktriage-20261110.db stacktriage:/data/backups/restore.db
docker run --rm --mount source=stacktriage-data,target=/data stacktriage:local stacktriage restore /data/backups/restore.db
docker run --rm --mount source=stacktriage-data,target=/data stacktriage:local stacktriage check
docker start stacktriage
curl http://127.0.0.1:8000/api/health
```

Check imported records in the UI after restoring. Preserve the off-host backup
and the safety copy until the restore is accepted. On Render, create an online
backup from the running service's Shell/SSH session with
`stacktriage backup /data/backups/DATE.db` and download it over SSH/SFTP. Do not
mistake a disk snapshot for a database-consistent application backup. Practice
restores locally before relying on a backup policy.

## Hosted deployment status

The repository includes deployment configuration, not proof of a currently running
hosted service. No public deployment URL or current host/account status can be
established from these files. The local container observations below are historical,
not a fresh deployment validation.

The Dockerfile requires a persistent writable `/data` directory. The committed
`render.yaml` declares one paid service instance and a persistent disk; do not
apply it without the owner's approval. Credentials, account authentication and
current provider pricing must be checked by the owner, not inferred from this repo.

### Owner steps for Render (paid-plan approval required)

1. Obtain explicit approval for the paid compute plan and persistent-disk charges.
   Review current pricing in Render before creating anything. The blueprint uses
   one `0.5c-512mb` instance and a 1 GiB disk; adjust capacity only with approval.
2. Push the completed repository, including `Dockerfile`, `.dockerignore`,
   `render.yaml` and the frontend lockfile, to
   <https://github.com/tanmay-alpha/StackTriage>. Do not push credentials or data.
3. Sign in to <https://dashboard.render.com/>, connect the GitHub repository, choose
   **New > Blueprint**, select the branch containing these files, and use the
   repository-root `render.yaml`. No CLI or API token is required for this UI path.
4. Review the resource preview: Docker web service `stacktriage`, one instance,
   paid plan, a persistent disk mounted at `/data`, database
   `/data/stacktriage.db`, port 8000, and health check `/api/health`. Do not select
   a free instance or remove the disk to bypass the cost.
5. Supply `STACKTRIAGE_API_KEY` when prompted (`sync: false`); use a new random
   secret of at least 16 characters. Confirm billing only after approval, then
   apply the blueprint. Leave Docker Command unset to use the Dockerfile command.
6. Watch build and startup logs. Confirm `/data` is writable by UID 10001. If the
   platform's disk ownership prevents startup, correct ownership via a controlled
   maintenance/root shell (`chown 10001:10001 /data`) before resuming; do not make
   the directory world-writable or put the database on ephemeral storage.
7. Only after Render reports the service healthy, visit its assigned HTTPS URL.
   Verify `/api/health` returns `{"status":"ok","auth_required":true}`, data
   requests without a key return 401, and an authenticated fixture upload and
   incident list work. Restart and redeploy, then confirm the same import remains.
   Record the actual URL and results before describing the deployment as successful.
8. Set `STACKTRIAGE_ALLOWED_HOSTS` to the assigned Render hostname and any approved
   custom hostname (include `127.0.0.1,localhost` for local health checks), then
   recheck health. Set up off-host backups and test restoration. The disk permits
   only one instance and introduces downtime during deployments. Automatic
   deployments are disabled in this blueprint; initiate future deploys manually.

Optional later split: after that backend exists, create a Vercel static frontend
project with root directory `frontend`, install command `npm ci`, build command
`npm run build`, and output directory `dist`. Configure an `/api/:path*` external
rewrite to `https://YOUR-RENDER-HOST/api/:path*` before the SPA fallback to
`/index.html`. Keep the API key entered at runtime and forwarded in request
headers, never in build variables. This is an owner-operated future step, not an
existing Vercel deployment.

References: [Render Blueprint fields](https://render.com/docs/blueprint-spec),
[Render persistent disks and limitations](https://render.com/docs/disks),
[Render Docker services](https://render.com/docs/docker).

## Local container verification (2026-11-10)

These are observed local results, not hosted results. The daemon was Docker
Desktop 4.96.3, Docker Engine 29.9.2, `linux/amd64`. The build command was:

```sh
docker build -t stacktriage:test .
```

It exited successfully in 136.38 seconds, including the actual frontend and
Python dependency installation steps. Selected build output:

```text
added 35 packages, and audited 36 packages in 1m
found 0 vulnerabilities
> tsc --noEmit && vite build
vite v8.3.7 building client environment for production...
44 modules transformed.
dist/index.html                   0.51 kB
dist/assets/index-Bgisv9aW.css   32.43 kB
dist/assets/index-BumhZL3l.js   348.10 kB
Successfully built stacktriage
Successfully installed ... fastapi-0.162.2 ... numpy-2.5.4 ... scikit-learn-1.9.1 ... sqlalchemy-2.1.8 ... stacktriage-0.1.0 ... uvicorn-0.54.0
naming to docker.io/library/stacktriage:test done
```

The dependency versions above describe this run, not a dependency lock or a
security audit. No Dockerfile repair was necessary; it already used `api,ml` and
built the frontend from its lockfile. `.dockerignore` was extended to exclude
local test artifacts, caches, environment files and SQLite sidecars.

A temporary random 43-character key was passed by environment, not committed.
The container used a named volume and a loopback-only test port:

```sh
docker volume create stacktriage-deploycheck-data
docker run -d --name stacktriage-deploycheck -p 127.0.0.1:18080:8000 --env STACKTRIAGE_API_KEY --mount source=stacktriage-deploycheck-data,target=/data stacktriage:test
```

Python `urllib.request` performed the HTTP requests. The upload used the raw bytes
of `tests/fixtures/spring_boot.log`, not multipart form data.

| Check | Observed result |
|---|---|
| `GET /api/health`, no key | 200, `{"status":"ok","auth_required":true}` |
| `GET /api/imports`, no key | 401, `{"detail":"missing or invalid API key"}` |
| `POST /api/imports?filename=spring_boot.log`, valid `X-API-Key`, `Content-Type: application/octet-stream` | 201; import ID 1, format `java`, 124579 bytes, 340 events, 4 incidents, `truncated:false`, `duplicate:false` |
| `GET /api/imports/1/incidents`, valid key | 200; total 4, occurrence counts 67, 17, 11 and 5 |
| `GET /`, no key | 200, `text/html; charset=utf-8`; title `StackTriage`, `<div id="root"></div>` and built asset references |
| `GET /assets/index-BumhZL3l.js`, no key | 200, JavaScript, 348102 bytes |
| `docker restart stacktriage-deploycheck` | Container restarted; import listing and incident listing exactly equal to their pre-restart JSON |
| Remove container and repeat the same `docker run` with the same volume | New container started; import and incident listings exactly equal to their original JSON |
| `docker run --name stacktriage-deploycheck-nokey stacktriage:test` | Exited 1; `RuntimeError: STACKTRIAGE_ENV=production requires STACKTRIAGE_API_KEY to be set` |

Persistence-check output:

```text
PERSIST_RESTART imports=1 import_id=1 events=340 incidents=4 identical=true
PERSIST_RECREATE imports=1 import_id=1 events=340 incidents=4 identical=true
PRODUCTION_NO_KEY_EXIT 1
PRODUCTION_NO_KEY_ERROR RuntimeError: STACKTRIAGE_ENV=production requires STACKTRIAGE_API_KEY to be set
CHECKS_PASSED
```

The verification harness had a reporting-only Python `TypeError` while formatting
the integer import ID after the restart comparison had passed. Formatting was
corrected in memory and execution resumed; no application change or second upload
was needed.

Cleanup was performed after verification:

```sh
docker rm -f -v stacktriage-deploycheck stacktriage-deploycheck-nokey
docker volume rm stacktriage-deploycheck-data
docker image rm stacktriage:test
```

Docker returned both container names and the volume name, followed by
`Untagged: stacktriage:test` and
`Deleted: sha256:e0e49925d3c9d59caf791837b917a9582754d4493b08edc2cec4780548d9cb76`.
The no-key container's anonymous volume was removed with `docker rm -v`.
Unrelated Docker resources and shared build caches were left intact.
