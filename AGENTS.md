# AGENTS.md — Project Briefing for AI Agents

## What this is

A single-service **SNMPv3 telemetry agent** (Python). It watches a directory for CSV report files, serves the metrics to an NMS via SNMPv3 (GET / GETNEXT / GETBULK, authPriv, UDP 10161), and hosts a small FastAPI app: a web dashboard (`/`), `/health`, `/metrics`, and an authenticated CSV `/upload` endpoint.

Production deployment: **Coolify** (Docker Compose, Caddy reverse proxy) at `https://snmp.cohogs.com`.

## Architecture (snmp_agent.py — intentionally one file)

| Component | What it does |
|---|---|
| `parse_csv_file()` | Parses a CSV and **atomically swaps** the served dataset (see invariants). |
| `ReportFileHandler` | watchdog observer on `WATCH_DIRECTORY` (`on_created`/`on_modified`/`on_moved`), runs on its own thread. |
| `DynamicMibController` | pysnmp instrumentation: exact GET + `read_next_variables` for walk/bulk over a sorted OID list. |
| Dynamic OID registry | Unknown CSV metrics auto-assigned `{BASE_OID}.{DYNAMIC_BRANCH}.N.0`, persisted to `OID_REGISTRY_PATH`. |
| FastAPI app | Dashboard (Jinja2, Flowbite/Tailwind via CDN), REST endpoints. Runs in the same asyncio loop as SNMP via `asyncio.gather`. |

## Invariants — do not break these

1. **Snapshot-replace semantics**: every successfully parsed CSV *replaces all* served data. Metrics missing from the newest file must stop being served (never serve stale values). A file that parses to **zero** metrics must be rejected (keep previous data, HTTP 422 for uploads).
2. **OID assignments are permanent**: never delete or reassign entries in the dynamic OID registry. NMS configs depend on stable OIDs. Fixed metrics in `METRIC_INDEX_MAP` must keep their OIDs.
3. **Thread safety via atomic swap**: mutate module-level dicts by building new ones and swapping references (watchdog thread writes, asyncio loop reads). No incremental mutation of the live dicts.
4. **Security**: `/upload` requires `X-API-Key` when `API_KEY` env is set; upload filenames must be basename-stripped + character-allowlisted (path traversal); size capped by `MAX_UPLOAD_BYTES`. Never log or commit real credentials — the defaults in code are lab values, overridable by env.
5. **Gauge scaling convention**: with `SNMP_VALUE_SYNTAX=gauge`, numeric values are served ×100 as Gauge32/Integer32. Document any change to this.
6. **Dependencies stay pinned** in `requirements.txt`; `Dockerfile` and `run.sh` must both install from it. pysnmp 7.x API (snake_case) is required — the codebase was already migrated.

## Repository layout

- `snmp_agent.py` — everything: SNMP server, watcher, dynamic OIDs, FastAPI.
- `templates/index.html` — dashboard (Flowbite 3.1.2 + Tailwind via pinned CDN, dark mode, vanilla JS polling `/metrics` and `/health`).
- `requirements.txt` — pinned deps (pysnmp==7.1.27, fastapi==0.140.0, …).
- `Dockerfile` — python:3.11-slim, copies `snmp_agent.py` + `templates/`.
- `docker-compose.yaml` — **standalone** local file (publishes 10161/udp + 8000).
- `docker-compose.coolify.yaml` — **standalone** Coolify file (Caddy labels, external `coolify` network). Do NOT combine the two files. In Coolify UI, Docker Compose Location = `/docker-compose.coolify.yaml`.
- `incoming_reports/` — watched dir, volume-mounted (also persists `oid_registry.json`; runtime files are gitignored).
- `README.md` — user docs: config env table, OID reference, dynamic OIDs, API.

## Development workflow

- **Run locally**: `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt` then `.venv/Scripts/python snmp_agent.py` (Windows) or `./run.sh` (Linux).
- **There is no automated test suite.** Verification is done live: start the agent, then exercise it with a pysnmp v3 client script (GET/walk/GETBULK on 127.0.0.1:10161) and `curl` against :8000. Do this after any behavior change.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`), granular, with a short body explaining the why.
- **Keep in sync**: when adding env vars → README config table + both compose files' comments; when adding metrics/OID behavior → README OID reference + this file; when adding runtime-generated files → `.gitignore`.

## Gotchas / environment notes

- **Coolify v4.2.0 bug** ([coollabsio/coolify#11030](https://github.com/coollabsio/coolify/issues/11030)): saving a docker-compose app's General settings crashes (`sslipDomainWarning` null). Domain is therefore hardcoded in the Caddy label in `docker-compose.coolify.yaml` instead of the UI.
- Inside Docker, auto-detected server IPs are container/public IPs — set `SERVER_IP` env to the host LAN IP the NMS should poll.
- SNMP (UDP 10161) is **not** proxied through Caddy — it goes direct to the host; the domain only fronts the HTTP API.
- The watcher fires before large files finish writing; `parse_csv_file` retries briefly when 0 metrics parse — keep that behavior.
