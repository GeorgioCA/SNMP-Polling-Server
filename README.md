# Python SNMPv3 CSV Telemetry Agent

An automated, lightweight SNMPv3 command responder written in Python. This service listens for incoming CSV telemetry report attachments, parses updated metrics in real time, and serves them to Network Management Systems (NMS) via SNMPv3 (GET, GETNEXT/walk and GETBULK supported).

---

## Directory Structure

```
.
├── snmp_agent.py               # Main Python SNMPv3 server, API and file watcher
├── requirements.txt            # Pinned Python dependencies
├── run.sh                      # Quick start script (creates venv, installs deps, runs server)
├── Dockerfile                  # Container definition
├── docker-compose.yaml         # Standalone Docker Compose setup
├── docker-compose.coolify.yaml # Standalone compose for Coolify/Caddy deployments
├── incoming_reports/           # Monitored directory for CSV report attachments
└── README.md                   # Documentation and operational guide
```

---

## Quick Start Options

### Option 1: Native Execution with `run.sh`

```bash
chmod +x run.sh
./run.sh
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python snmp_agent.py
```

### Option 2: Docker / Docker Compose (standalone)

```bash
docker compose up --build -d
```

The `./incoming_reports` folder on your host machine is mapped directly into the container. SNMP is published on UDP `10161` and the HTTP API on TCP `8000`.

### Option 3: Coolify (Caddy reverse proxy)

In the Coolify UI, set **Docker Compose Location** to `/docker-compose.coolify.yaml` — it is a fully self-contained file (do not combine it with the base `docker-compose.yaml`). From the CLI:

```bash
docker network create coolify   # once, if it does not exist yet
docker compose -f docker-compose.coolify.yaml up --build -d
```

> **Warning:** this setup exposes the HTTP API at your public FQDN. Set `API_KEY` (below) in Coolify's Environment Variables, or anyone can replace your served metrics.

---

## Configuration (environment variables)

| Variable             | Default               | Description                                                        |
| -------------------- | --------------------- | ------------------------------------------------------------------ |
| `API_KEY`            | *(unset)*             | API key required on `POST /upload` via `X-API-Key` header. **Set in production** — if unset, upload is unauthenticated. |
| `SNMPV3_USER`        | `snmpv3user`          | SNMPv3 username                                                    |
| `SNMPV3_AUTH_KEY`    | `AuthSecretKey123`    | SNMPv3 authentication passphrase (SHA)                             |
| `SNMPV3_PRIV_KEY`    | `PrivSecretKey123`    | SNMPv3 privacy passphrase (AES128)                                 |
| `BASE_OID`           | `1.3.6.1.4.1.99999`   | Enterprise subtree the metrics are served under                    |
| `SNMP_PORT`          | `10161`               | UDP port for SNMP                                                  |
| `API_PORT`           | `8000`                | TCP port for the HTTP API                                          |
| `WATCH_DIRECTORY`    | `./incoming_reports`  | Directory watched for CSV reports                                  |
| `SNMP_VALUE_SYNTAX`  | `string`              | `string` = OctetString (`"611.31"`); `gauge` = Gauge32 scaled ×100 (`61131`) so NMS tools can graph values natively |
| `MAX_UPLOAD_BYTES`   | `10485760`            | Max accepted upload size                                           |
| `DYNAMIC_BRANCH`     | `100`                 | Sub-branch under `BASE_OID` for auto-assigned OIDs of unknown metrics |
| `MAX_DYNAMIC_OIDS`   | `1000`                | Maximum number of dynamically assigned metric OIDs                 |
| `OID_REGISTRY_PATH`  | `./incoming_reports/oid_registry.json` | Where dynamic OID assignments are persisted       |

Each uploaded/dropped CSV is a **full snapshot**: metrics missing from the newest file stop being served rather than going stale.

---

## SNMPv3 Credentials & Testing

Defaults (override via environment, see above):

- **Username**: `snmpv3user`
- **Security Level**: `authPriv`
- **Authentication Protocol**: `SHA` / **Passphrase**: `AuthSecretKey123`
- **Privacy Protocol**: `AES128` / **Passphrase**: `PrivSecretKey123`
- **Port**: `10161` (UDP)

### Query Test

```bash
snmpget -v3 \
  -l authPriv \
  -u snmpv3user \
  -a SHA -A AuthSecretKey123 \
  -x AES -X PrivSecretKey123 \
  127.0.0.1:10161 \
  1.3.6.1.4.1.99999.1.1.0
```

### Walk the whole subtree

```bash
snmpwalk -v3 \
  -l authPriv \
  -u snmpv3user \
  -a SHA -A AuthSecretKey123 \
  -x AES -X PrivSecretKey123 \
  127.0.0.1:10161 \
  1.3.6.1.4.1.99999
```

---

## OID Reference

All metrics are served under `BASE_OID` (default `1.3.6.1.4.1.99999`) as scalar instances (ending in `.0`). If you change `BASE_OID`, only the prefix changes — the last three arcs stay the same.

| Metric              | OID (default prefix)          | Description                    |
| ------------------- | ----------------------------- | ------------------------------ |
| `Volts_Line_AB`     | `1.3.6.1.4.1.99999.1.1.0`     | Voltage, phase A–B (V)         |
| `Volts_Line_BC`     | `1.3.6.1.4.1.99999.1.2.0`     | Voltage, phase B–C (V)         |
| `Volts_Line_CA`     | `1.3.6.1.4.1.99999.1.3.0`     | Voltage, phase C–A (V)         |
| `Volts_Line_avgLL`  | `1.3.6.1.4.1.99999.1.4.0`     | Average line-to-line voltage   |
| `Volts_Line_AN`     | `1.3.6.1.4.1.99999.1.5.0`     | Voltage, phase A–neutral (V)   |
| `Volts_Line_BN`     | `1.3.6.1.4.1.99999.1.6.0`     | Voltage, phase B–neutral (V)   |
| `Volts_Line_CN`     | `1.3.6.1.4.1.99999.1.7.0`     | Voltage, phase C–neutral (V)   |
| `Volts_Line_avgLN`  | `1.3.6.1.4.1.99999.1.8.0`     | Average line-to-neutral voltage|
| `Amps_IA`           | `1.3.6.1.4.1.99999.2.1.0`     | Current, phase A (A)           |
| `Amps_IB`           | `1.3.6.1.4.1.99999.2.2.0`     | Current, phase B (A)           |
| `Amps_IC`           | `1.3.6.1.4.1.99999.2.3.0`     | Current, phase C (A)           |
| `Amps_IN`           | `1.3.6.1.4.1.99999.2.4.0`     | Neutral current (A)            |
| `Amps_Iavg`         | `1.3.6.1.4.1.99999.2.5.0`     | Average phase current (A)      |
| `RealPower`         | `1.3.6.1.4.1.99999.3.1.0`     | Real (active) power (W)        |
| `ApparentPower`     | `1.3.6.1.4.1.99999.3.2.0`     | Apparent power (VA)            |
| `ReactivePower`     | `1.3.6.1.4.1.99999.3.3.0`     | Reactive power (VAR)           |
| `Frequency`         | `1.3.6.1.4.1.99999.4.1.0`     | Line frequency (Hz)            |

Values are `OctetString` by default (e.g. `"611.31"`). With `SNMP_VALUE_SYNTAX=gauge` they are served as `Gauge32` scaled ×100 (e.g. `61131`).

### Dynamic OIDs

Metrics found in a CSV that are **not** in the fixed table above are automatically assigned an OID under the dynamic branch:

```
{BASE_OID}.100.{N}.0     N = 1, 2, 3, ... (first-come-first-served)
```

Assignments are persisted to `incoming_reports/oid_registry.json` (volume-mounted, so it survives container rebuilds), which means:

- A metric keeps the **same OID forever**, across restarts and future uploads — your NMS configuration stays valid.
- Uploading a CSV with a brand-new metric (e.g. `PowerFactor,0.97`) makes it immediately queryable and walkable — no code change or restart needed.
- Rows whose value column reads `Value` are treated as headers and skipped.
- Discover current assignments via `snmpwalk` on the base OID, `GET /metrics`, or the registry file itself.
- Limits: up to `MAX_DYNAMIC_OIDS` (default 1000) dynamic metrics; the branch number is configurable via `DYNAMIC_BRANCH`.

---

## HTTP API

| Endpoint      | Method | Auth                | Description                          |
| ------------- | ------ | ------------------- | ------------------------------------ |
| `/health`     | GET    | none                | Service status and metric count      |
| `/metrics`    | GET    | none                | Dump currently served metrics/OIDs   |
| `/upload`     | POST   | `X-API-Key` header* | Upload a CSV report (multipart form) |

\* only when `API_KEY` is set.

```bash
curl -X POST http://127.0.0.1:8000/upload \
  -H "X-API-Key: change-me" \
  -F "file=@report.csv"
```

The watcher also picks up CSVs copied, moved or modified in place inside `incoming_reports/`.
