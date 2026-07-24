# Python SNMPv3 CSV Telemetry Agent

An automated, lightweight SNMPv3 command responder written in Python. This service listens for incoming CSV telemetry report attachments, parses updated metrics in real time, and serves them to Network Management Systems (NMS) via SNMPv3.

---

## Directory Structure

.
├── snmp_agent.py          # Main Python SNMPv3 server and file watcher
├── run.sh                 # Quick start script (creates venv, updates pip, runs server)
├── Dockerfile             # Container definition
├── docker-compose.yml     # Docker Compose setup
├── incoming_reports/      # Monitored directory for CSV report attachments
└── README.md              # Documentation and operational guide

---

## Quick Start Options

### Option 1: Native Execution with `run.sh`

chmod +x run.sh
./run.sh

---

### Option 2: Docker / Docker Compose

docker compose up --build -d

The `./incoming_reports` folder on your host machine is mapped directly into the container.

---

## SNMPv3 Credentials & Testing

- **Username**: `snmpv3user`
- **Security Level**: `authPriv`
- **Authentication Protocol**: `SHA`
- **Authentication Passphrase**: `AuthSecretKey123`
- **Privacy Protocol**: `AES128`
- **Privacy Passphrase**: `PrivSecretKey123`
- **Port**: `10161` (UDP)

### Query Test

snmpget -v3 \
  -l authPriv \
  -u snmpv3user \
  -a SHA -A AuthSecretKey123 \
  -x AES -X PrivSecretKey123 \
  127.0.0.1:10161 \
  1.3.6.1.4.1.99999.1.1.0