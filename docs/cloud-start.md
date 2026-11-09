# Sporton cloud environment · v0.3

Use /workspace/sporton-data and /workspace/.venvs/sporton/bin/python. Preserve all existing source changes, ignored var/, SQLite, PostgreSQL data, credentials and backups. Do not reset data or create a worktree unless explicitly requested. Read the managed cloud runtime skill for proxy/CA/Docker rules. Never print credentials, disable TLS, bypass an access denial, or send external messages without authorization.

## Install and start

Run bash tools/install.sh if dependencies need installation; it retains the SQLite compatibility database. Then run with the venv Python:

    python tools/prepare_persistence.py
    python tools/start_postgres.py
    python tools/postgres_init.py

prepare_persistence creates private development files only once; it preserves existing passwords/key/token. start_postgres uses the explicit local Docker socket, pinned official PostgreSQL digest and persistent var/postgres bind mounts; it refuses to replace a mismatching existing container. Never delete the existing data to resolve an error. postgres_init runs owner DDL and restricts sporton_app to SELECT/INSERT. These are local development credentials, not production secrets. Networked Docker builds need the session proxy and BuildKit CA secret as described in Dockerfile/compose.yaml. App image build and production Compose deployment are not yet validated.

Use tools/runtime.py for development processes (file-based secrets, no values logged):

    python tools/runtime.py sporton serve --host 127.0.0.1 --port 8000
    python tools/runtime.py sporton briefing-daemon --inbox var/inbox --retrain
    python tools/runtime.py --owner sporton persistence-worker

Start each in a managed command session; inspect existing responses/locks before creating duplicates. Processes do not survive snapshots. A Docker restart policy alone does not restart the Python API or daemons. No always-on guarantee or ChatGPT message scheduler is connected. Dashboard delivery was explicitly selected by the user. Daily slots remain 06:30/17:00 Asia/Seoul. Late/missed slots are labeled with actual creation time; never backdate predictions. The separate persistence worker handles encrypted backups and actual restore tests; the briefing process only records delegation.

Check /health, /api/capabilities, /api/overview?league=NBA, /api/briefing-status, /api/briefings and /. /admin is a token entry form; /api/admin/status and /api/admin/reports/{id} require the token from var/persistence-dev/admin.token. Keep it out of logs, URLs and browser persistent storage. Use local HTTP clients with trust_env=False for loopback only; preserve external proxy/TLS. Do not create localhost preview links in onboarding.

## Storage, verification and migration

PostgreSQL is the active development DB with 46 append-only event tables plus seven typed FK/statistics tables. SQLite remains a preserved legacy/test path. Production requires external PostgreSQL TLS sslmode=verify-full, independent backup storage and separately managed keys. var/ on this host is not proof of survival after environment/host deletion. Never claim off-host persistence, production deployment or fresh-task restoration without independently testing it.

The actual legacy SQLite-to-PostgreSQL migration preserved IDs, creation times and payload hashes. tools/upgrade_storage.py is an explicit migration/export/research-registration tool, not a startup command; running it can append another research record. Do not run it on every startup. Read docs/persistence.md and docs/backup-recovery.md first. PRE_GAME originals are immutable; corrections of fixtures/results append evidence-bearing versions and new audits. Before a historical feature cutoff, later corrections are unavailable. Source publication times must not be fabricated.

Backup configuration is config/persistence.dev.json (60-minute interval/RPO, 30-day retention, 300-second drill RTO). Use owner commands secure-backup and restore-test BACKUP_ID for a manual drill. Created backups remain CREATED_UNVERIFIED until actual isolated restore validates rows, schema constraints/triggers/functions, references, files and Parquet. A later failed test invalidates current verified status. Worker retries failures with backoff and retains at least two verified sets. AES-256-GCM key stays outside backup root. WAL staging/encryption is implemented, but no physical base backup or PITR target restore has been tested: pitr_restore_tested=false. Same-host backup is not remote backup.

Run all tests from the checkout:

    SPORTON_TEST_POSTGRES_ADMIN_URL_FILE=/workspace/sporton-data/var/persistence-dev/test-admin.url /workspace/.venvs/sporton/bin/python -m pytest -q

2026-11-06 verification: 96 passed, including four tests against real PostgreSQL. The PG tests create/drop only random scratch DBs; never point them at a production administrator. SQLite tests use independent temporary SPORTON_HOME/database. tools/verify_pg_restart.py deliberately restarts only the recognized development container and checks all table hashes. It is a maintenance check, not a routine startup action. No test performance represents live accuracy. Dependency warnings are documented.

## Data and models

NBA, football and baseball have distinct statistical analysts/features/models/review rules and league-isolated Champion/Challenger history. KBL/volleyball/esports have schema preparation but no completed live models. Do not reuse NBA weights for KBL. Generated LLM specialist/reviewer services are not connected; deterministic validation and separate statistical critical evidence are implemented. Read docs/multisport.md and docs/model-evaluation.md.

Actual licensed archives: 59,008 historical NBA games and 1,140 EPL games across 2023-24/2024-25/2025-26, exported to Parquet and verified in DuckDB. Raw bytes/license evidence/hash remain in var/raw and DB metadata. They are date_only_archive, not verified pre-game data: precise publication/tipoff metadata is absent. Source observations remain UNVERIFIED when publication is unknown. Verified live games, fixtures, production models and official priced picks remain absent. Never invent today's sports analysis, player teams, injuries, markets or quarter scores.

PyTorch 2.9.0+cpu is pinned in requirements-deep-learning.lock. The EPL retrospective research experiment uses chronological fit/calibration/validation/test blocks of 349/198/173/374 and fixed candidates. Final research Brier: MLP 0.3264 vs Poisson 0.3171; MLP did not improve and was not promoted. Reuse sealed experiments rather than retuning the same final test. Research artifacts are not production model_versions. Missing advanced metrics remain null.

The network draft includes NBA/KBL/Naver/Daum/ESPN/search domains; saving it does not apply it. Applied custom egress has not been confirmed to include these hosts. Proxy 403 is not evidence of the provider's anti-crawler policy. After the user reviews/saves and publishes environment settings, retry lawful source requests and inspect actual responses; provider terms, extraction and field verification remain necessary. Do not use relays to bypass policy. Public GitHub archives remain separate from live feeds. collect-nba raw LeagueGameLog alone is not enough for verified publication/tipoff/period metadata.

The briefing daemon performs cadence-aware raw collection, CSV inbox ingestion, result audits and gated league-specific challenger training. Only sufficient verified samples enable production learning. It tracks previous briefing topics/results and upcoming registered fixtures; arbitrary chat history is not automatically imported. Use briefing-note to preserve a topic. External notifications are not configured. Read docs/briefings.md, docs/collection-and-deep-learning.md, docs/roadmap.md and docs/validation.md for exact limitations.

Saving these instructions creates a draft only. It does not publish configuration, deploy production services, or establish off-host restoration.
