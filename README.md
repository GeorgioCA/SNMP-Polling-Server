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
├── docker-compose.coolify.yaml # Coolify/Caddy reverse-proxy override
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

```bash
docker network create coolify   # once, if it does not exist yet
docker compose -f docker-compose.yaml -f docker-compose.coolify.yaml up --build -d
```

> **Warning:** the Coolify override exposes the HTTP API at your public FQDN. Set `API_KEY` (below) or anyone can replace your served metrics.

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
