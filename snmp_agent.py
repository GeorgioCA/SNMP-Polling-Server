import os
import re
import csv
import glob
import json
import time
import logging
import asyncio
from datetime import datetime
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.proto.api import v2c
from pysnmp.smi import instrum, exval

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Header

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Configuration (environment overrides) ────────────────────────────────────

WATCH_DIRECTORY = os.environ.get("WATCH_DIRECTORY", "./incoming_reports")
BASE_OID = os.environ.get("BASE_OID", "1.3.6.1.4.1.99999")
SNMP_PORT = int(os.environ.get("SNMP_PORT", "10161"))
API_PORT = int(os.environ.get("API_PORT", "8000"))

SNMPV3_USER = os.environ.get("SNMPV3_USER", "snmpv3user")
SNMPV3_AUTH_KEY = os.environ.get("SNMPV3_AUTH_KEY", "AuthSecretKey123")
SNMPV3_PRIV_KEY = os.environ.get("SNMPV3_PRIV_KEY", "PrivSecretKey123")

# API key required on POST /upload (header: X-API-Key).
# If unset, /upload is UNAUTHENTICATED — only acceptable for local development.
API_KEY = os.environ.get("API_KEY")

# Value syntax served over SNMP:
#   "string" (default) -> OctetString, e.g. "611.31" (backwards compatible)
#   "gauge"            -> Gauge32 scaled x100, e.g. 61131 (NMS-graphable)
VALUE_SYNTAX = os.environ.get("SNMP_VALUE_SYNTAX", "string").lower()

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

METRIC_INDEX_MAP = {
    "Volts_Line_AB": f"{BASE_OID}.1.1.0",
    "Volts_Line_BC": f"{BASE_OID}.1.2.0",
    "Volts_Line_CA": f"{BASE_OID}.1.3.0",
    "Volts_Line_avgLL": f"{BASE_OID}.1.4.0",
    "Volts_Line_AN": f"{BASE_OID}.1.5.0",
    "Volts_Line_BN": f"{BASE_OID}.1.6.0",
    "Volts_Line_CN": f"{BASE_OID}.1.7.0",
    "Volts_Line_avgLN": f"{BASE_OID}.1.8.0",
    "Amps_IA": f"{BASE_OID}.2.1.0",
    "Amps_IB": f"{BASE_OID}.2.2.0",
    "Amps_IC": f"{BASE_OID}.2.3.0",
    "Amps_IN": f"{BASE_OID}.2.4.0",
    "Amps_Iavg": f"{BASE_OID}.2.5.0",
    "RealPower": f"{BASE_OID}.3.1.0",
    "ApparentPower": f"{BASE_OID}.3.2.0",
    "ReactivePower": f"{BASE_OID}.3.3.0",
    "Frequency": f"{BASE_OID}.4.1.0",
}

# ── Dynamic OIDs ─────────────────────────────────────────────────────────────
# Metrics found in a CSV that are not in METRIC_INDEX_MAP are auto-assigned an
# OID under {BASE_OID}.{DYNAMIC_BRANCH}.N.0. Assignments are persisted in a
# registry file (first-come-first-served), so a metric keeps its OID across
# restarts and future uploads — NMS configurations stay valid.
DYNAMIC_BRANCH = int(os.environ.get("DYNAMIC_BRANCH", "100"))
MAX_DYNAMIC_OIDS = int(os.environ.get("MAX_DYNAMIC_OIDS", "1000"))
OID_REGISTRY_PATH = os.environ.get(
    "OID_REGISTRY_PATH", os.path.join(WATCH_DIRECTORY, "oid_registry.json")
)

# Served data. parse_csv_file() swaps these references atomically, so readers
# (SNMP event loop) always see a consistent snapshot even though the file
# watcher runs on its own thread.
METRICS_CACHE = {}
METRIC_OID_MAP = {}  # metric name -> oid str (fixed + dynamic)
OID_MAP = {}
OID_LIST = []  # [(oid_tuple, oid_str)], sorted — used for GETNEXT/GETBULK
OID_REGISTRY = {}  # dynamic metric name -> oid str, persisted to disk


def load_oid_registry():
    global OID_REGISTRY
    try:
        with open(OID_REGISTRY_PATH, "r", encoding="utf-8") as f:
            OID_REGISTRY = json.load(f)
    except FileNotFoundError:
        OID_REGISTRY = {}
        return
    except (json.JSONDecodeError, OSError) as e:
        logging.error(f"Cannot read {OID_REGISTRY_PATH}: {e}; starting with empty registry")
        OID_REGISTRY = {}
        return
    # Drop stale entries if BASE_OID / DYNAMIC_BRANCH changed
    prefix = f"{BASE_OID}.{DYNAMIC_BRANCH}."
    stale = [m for m, o in OID_REGISTRY.items() if not o.startswith(prefix)]
    for m in stale:
        logging.warning(f"Dropping stale dynamic OID for {m!r} (registry prefix changed)")
        del OID_REGISTRY[m]
    if OID_REGISTRY:
        logging.info(f"Loaded {len(OID_REGISTRY)} dynamic OID assignments from {OID_REGISTRY_PATH}")


def save_oid_registry():
    tmp_path = OID_REGISTRY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(OID_REGISTRY, f, indent=2, sort_keys=True)
    os.replace(tmp_path, OID_REGISTRY_PATH)


def _dynamic_oid(metric):
    """Return (oid, registry_changed) for a non-fixed metric."""
    if metric in OID_REGISTRY:
        return OID_REGISTRY[metric], False
    if len(OID_REGISTRY) >= MAX_DYNAMIC_OIDS:
        return None, False
    used = {int(o.rsplit(".", 2)[-2]) for o in OID_REGISTRY.values()}
    arc = 1
    while arc in used:
        arc += 1
    OID_REGISTRY[metric] = f"{BASE_OID}.{DYNAMIC_BRANCH}.{arc}.0"
    logging.info(f"Assigned dynamic OID {OID_REGISTRY[metric]} to new metric {metric!r}")
    return OID_REGISTRY[metric], True


def _oid_tuple(oid_str):
    return tuple(int(part) for part in oid_str.split("."))


def _to_smi_value(value_str):
    """Convert a CSV string to an SNMP value according to VALUE_SYNTAX."""
    if VALUE_SYNTAX == "gauge":
        try:
            scaled = round(float(value_str) * 100)
            if scaled < 0:
                return v2c.Integer32(scaled)
            return v2c.Gauge32(scaled)
        except (ValueError, OverflowError):
            pass
    return v2c.OctetString(value_str)


def parse_csv_file(file_path, attempts=5, delay=0.5):
    """Parse a CSV report and atomically REPLACE the served metrics.

    Each report is a full snapshot: metrics absent from the new file stop
    being served instead of going stale. Unknown metrics are auto-assigned
    persistent OIDs under the dynamic branch (see load_oid_registry).
    Retries briefly when nothing is parsed, because watchdog fires
    on_created before writers finish. Returns the number of metrics loaded.
    """
    metrics = {}
    for attempt in range(attempts):
        metrics = {}
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                for row in csv.reader(f):
                    if len(row) != 2:
                        continue
                    metric, value = row[0].strip(), row[1].strip()
                    if not metric or not value:
                        continue
                    if value.lower() == "value":
                        continue  # header row, e.g. "Main Switch Board,Value"
                    metrics[metric] = value
        except OSError as e:
            logging.warning(f"Cannot read {file_path}: {e}")
        if metrics:
            break
        if attempt < attempts - 1:
            time.sleep(delay)

    if not metrics:
        logging.error(f"No metrics parsed from {file_path}; keeping previous data")
        return 0

    # Resolve OIDs: fixed map first, dynamic registry for everything else
    metric_oids = {}
    registry_changed = False
    for m in metrics:
        if m in METRIC_INDEX_MAP:
            metric_oids[m] = METRIC_INDEX_MAP[m]
        else:
            oid, changed = _dynamic_oid(m)
            registry_changed |= changed
            if oid is not None:
                metric_oids[m] = oid
            else:
                logging.warning(f"Dynamic OID limit ({MAX_DYNAMIC_OIDS}) reached; skipping metric {m!r}")
    if registry_changed:
        save_oid_registry()

    global METRICS_CACHE, METRIC_OID_MAP, OID_MAP, OID_LIST
    METRICS_CACHE = {m: v for m, v in metrics.items() if m in metric_oids}
    METRIC_OID_MAP = metric_oids
    OID_MAP = {metric_oids[m]: v for m, v in METRICS_CACHE.items()}
    OID_LIST = sorted((_oid_tuple(o), o) for o in OID_MAP)
    logging.info(f"Loaded {len(METRICS_CACHE)} metrics from {os.path.basename(file_path)} (previous data replaced)")
    return len(METRICS_CACHE)


class ReportFileHandler(FileSystemEventHandler):
    def _maybe_parse(self, path):
        if path.endswith(".csv"):
            parse_csv_file(path)

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_parse(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._maybe_parse(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._maybe_parse(event.dest_path)


class DynamicMibController(instrum.AbstractMibInstrumController):
    def read_variables(self, *var_binds, **context):
        res = []
        for oid, _val in var_binds:
            oid_str = str(oid)
            value_str = OID_MAP.get(oid_str)
            if value_str is not None:
                res.append((oid, _to_smi_value(value_str)))
            else:
                logging.debug(f"read_variables: OID {oid_str!r} not found")
                res.append((oid, exval.noSuchInstance))
        return res

    def read_next_variables(self, *var_binds, **context):
        res = []
        for oid, _val in var_binds:
            oid_tuple = _oid_tuple(str(oid))
            next_entry = next((e for e in OID_LIST if e[0] > oid_tuple), None)
            if next_entry is None:
                res.append((oid, exval.endOfMibView))
            else:
                next_tuple, next_str = next_entry
                res.append((v2c.ObjectIdentifier(next_tuple), _to_smi_value(OID_MAP[next_str])))
        return res


SAMPLE_CSV = """Main Switch Board,Value
Volts_Line_AB,120.5
Volts_Line_BC,119.8
Volts_Line_CA,121.1
Volts_Line_avgLL,120.5
Volts_Line_AN,69.3
Volts_Line_BN,69.1
Volts_Line_CN,69.5
Volts_Line_avgLN,69.3
Amps_IA,15.2
Amps_IB,14.8
Amps_IC,15.1
Amps_IN,0.3
Amps_Iavg,15.0
RealPower,5400
ApparentPower,5600
ReactivePower,1200
Frequency,60.0
"""

# ── FastAPI app ──────────────────────────────────────────────────────────────

api = FastAPI(title="SNMP Agent", version="1.1.0")

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


async def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


@api.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "metrics_loaded": len(METRICS_CACHE), "watch_dir": WATCH_DIRECTORY}


@api.get("/metrics")
async def list_metrics():
    """List all currently loaded metrics."""
    return {
        "count": len(METRICS_CACHE),
        "metrics": {k: {"value": v, "oid": METRIC_OID_MAP.get(k)} for k, v in METRICS_CACHE.items()},
        "oid_map": dict(OID_MAP),
        "dynamic_registry": dict(OID_REGISTRY),
    }


@api.post("/upload", dependencies=[Depends(require_api_key)])
async def upload_csv(file: UploadFile = File(...)):
    """Upload a CSV report. Replaces all served data with the new file's metrics."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted")

    # Strip any directory components and unsafe characters (path traversal guard)
    safe_name = _SAFE_CHARS.sub("_", os.path.basename(file.filename))
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(400, "Invalid file name")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES} bytes")

    os.makedirs(WATCH_DIRECTORY, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    save_path = os.path.join(WATCH_DIRECTORY, f"upload-{timestamp}-{safe_name}")

    with open(save_path, "wb") as f:
        f.write(content)

    # Parse directly so the caller gets immediate feedback
    parsed = parse_csv_file(save_path)
    if parsed == 0:
        raise HTTPException(422, "CSV contained no known metrics; existing data was kept")

    return {
        "status": "ok",
        "file": file.filename,
        "saved_as": save_path,
        "metrics_loaded": parsed,
    }


def load_most_recent_file(folder):
    csv_files = glob.glob(os.path.join(folder, "*.csv"))
    if csv_files:
        latest_file = max(csv_files, key=os.path.getmtime)
        parse_csv_file(latest_file)
    else:
        logging.warning(f"No CSV files found in {folder}. Creating sample data.")
        sample_path = os.path.join(folder, "sample.csv")
        with open(sample_path, "w") as f:
            f.write(SAMPLE_CSV)
        parse_csv_file(sample_path)


async def main():
    os.makedirs(WATCH_DIRECTORY, exist_ok=True)
    load_oid_registry()
    load_most_recent_file(WATCH_DIRECTORY)

    if not API_KEY:
        logging.warning("API_KEY is not set — POST /upload is UNAUTHENTICATED. Set API_KEY in production.")

    event_handler = ReportFileHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIRECTORY, recursive=False)
    observer.start()

    snmp_engine = engine.SnmpEngine()

    # Pre-load MIBs needed for SNMPv3 user and VACM setup
    mib_builder = snmp_engine.get_mib_builder()
    mib_builder.load_modules('SNMP-VIEW-BASED-ACM-MIB')
    mib_builder.load_modules('SNMP-USER-BASED-SM-MIB')
    mib_builder.load_modules('PYSNMP-USM-MIB')
    mib_builder.load_modules('__PYSNMP-USM-MIB')

    config.add_transport(
        snmp_engine,
        udp.DOMAIN_NAME,
        udp.UdpTransport().open_server_mode(("0.0.0.0", SNMP_PORT))
    )

    config.add_v3_user(
        snmp_engine,
        userName=SNMPV3_USER,
        authProtocol=config.USM_AUTH_HMAC96_SHA,
        authKey=SNMPV3_AUTH_KEY,
        privProtocol=config.USM_PRIV_CFB128_AES,
        privKey=SNMPV3_PRIV_KEY
    )

    config.add_context(snmp_engine, "")
    config.add_vacm_group(snmp_engine, "v3group", 3, SNMPV3_USER)
    config.add_vacm_access(snmp_engine, "v3group", "", 3, 3, "exact", "readView", "", "")
    config.add_vacm_view(snmp_engine, "readView", "included", BASE_OID, "")

    snmp_context = context.SnmpContext(snmp_engine)
    cmdrsp.GetCommandResponder(snmp_engine, snmp_context)
    cmdrsp.NextCommandResponder(snmp_engine, snmp_context)
    cmdrsp.BulkCommandResponder(snmp_engine, snmp_context)
    snmp_context.get_mib_instrum = lambda ctxName=b"": DynamicMibController()

    logging.info(f"SNMPv3 Server listening on UDP 0.0.0.0:{SNMP_PORT}...")
    logging.info(f"Serving {len(OID_MAP)} metrics under {BASE_OID} (syntax: {VALUE_SYNTAX})")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        observer.stop()
        observer.join()
        raise


if __name__ == "__main__":
    import uvicorn

    # Run SNMP agent + FastAPI in the same event loop
    uvicorn_cfg = uvicorn.Config(api, host="0.0.0.0", port=API_PORT, log_config=None)
    server = uvicorn.Server(uvicorn_cfg)

    async def run_all():
        await asyncio.gather(main(), server.serve())

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logging.info("Shutting down.")
