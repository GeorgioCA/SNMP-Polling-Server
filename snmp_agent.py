import os
import csv
import glob
import logging
import asyncio
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.proto.api import v2c
from pysnmp.smi import instrum, exval

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WATCH_DIRECTORY = "./incoming_reports"
BASE_OID = "1.3.6.1.4.1.99999"

METRICS_CACHE = {}
OID_MAP = {}

METRIC_INDEX_MAP = {
    "Volts_Line_AB": "1.3.6.1.4.1.99999.1.1.0",
    "Volts_Line_BC": "1.3.6.1.4.1.99999.1.2.0",
    "Volts_Line_CA": "1.3.6.1.4.1.99999.1.3.0",
    "Volts_Line_avgLL": "1.3.6.1.4.1.99999.1.4.0",
    "Volts_Line_AN": "1.3.6.1.4.1.99999.1.5.0",
    "Volts_Line_BN": "1.3.6.1.4.1.99999.1.6.0",
    "Volts_Line_CN": "1.3.6.1.4.1.99999.1.7.0",
    "Volts_Line_avgLN": "1.3.6.1.4.1.99999.1.8.0",
    "Amps_IA": "1.3.6.1.4.1.99999.2.1.0",
    "Amps_IB": "1.3.6.1.4.1.99999.2.2.0",
    "Amps_IC": "1.3.6.1.4.1.99999.2.3.0",
    "Amps_IN": "1.3.6.1.4.1.99999.2.4.0",
    "Amps_Iavg": "1.3.6.1.4.1.99999.2.5.0",
    "RealPower": "1.3.6.1.4.1.99999.3.1.0",
    "ApparentPower": "1.3.6.1.4.1.99999.3.2.0",
    "ReactivePower": "1.3.6.1.4.1.99999.3.3.0",
    "Frequency": "1.3.6.1.4.1.99999.4.1.0",
}

def parse_csv_file(file_path):
    logging.info(f"Parsing CSV file: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) == 2:
                    metric, value = row[0].strip(), row[1].strip()
                    if metric in METRIC_INDEX_MAP:
                        METRICS_CACHE[metric] = value
                        oid = METRIC_INDEX_MAP[metric]
                        OID_MAP[oid] = value
        logging.info(f"Successfully loaded {len(METRICS_CACHE)} metrics into memory.")
    except Exception as e:
        logging.error(f"Error reading CSV {file_path}: {e}")

class ReportFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".csv"):
            parse_csv_file(event.src_path)

class DynamicMibController(instrum.AbstractMibInstrumController):
    def read_variables(self, *varBinds, **context):
        res = []
        for oid_val in varBinds:
            oid, val = oid_val[0], oid_val[1]
            oid_str = str(oid)
            logging.debug(f"read_variables: oid={oid_str!r}")
            if oid_str in OID_MAP:
                value_str = OID_MAP[oid_str]
                res.append((oid, v2c.OctetString(value_str)))
            else:
                logging.debug(f"read_variables: OID {oid_str!r} not found")
                res.append((oid, exval.noSuchInstance))
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

api = FastAPI(title="SNMP Agent", version="1.0.0")


@api.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "metrics_loaded": len(METRICS_CACHE), "watch_dir": WATCH_DIRECTORY}


@api.get("/metrics")
async def list_metrics():
    """List all currently loaded metrics."""
    return {
        "count": len(METRICS_CACHE),
        "metrics": {k: {"value": v, "oid": METRIC_INDEX_MAP[k]} for k, v in METRICS_CACHE.items()},
        "oid_map": dict(OID_MAP),
    }


@api.post("/upload")
async def upload_csv(file: UploadFile = File(...)):
    """Upload a CSV report. Replaces existing data with the new file's metrics."""
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted")

    os.makedirs(WATCH_DIRECTORY, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    save_path = os.path.join(WATCH_DIRECTORY, f"upload-{timestamp}-{file.filename}")

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # Parse directly so the caller gets immediate feedback
    parse_csv_file(save_path)

    return {
        "status": "ok",
        "file": file.filename,
        "saved_as": save_path,
        "metrics_loaded": len(METRICS_CACHE),
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
    load_most_recent_file(WATCH_DIRECTORY)

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
        udp.UdpTransport().open_server_mode(("0.0.0.0", 10161))
    )

    config.add_v3_user(
        snmp_engine,
        userName="snmpv3user",
        authProtocol=config.USM_AUTH_HMAC96_SHA,
        authKey="AuthSecretKey123",
        privProtocol=config.USM_PRIV_CFB128_AES,
        privKey="PrivSecretKey123"
    )

    config.add_context(snmp_engine, "")
    config.add_vacm_group(snmp_engine, "v3group", 3, "snmpv3user")
    config.add_vacm_access(snmp_engine, "v3group", "", 3, 3, "exact", "readView", "", "")
    config.add_vacm_view(snmp_engine, "readView", "included", BASE_OID, "")

    snmp_context = context.SnmpContext(snmp_engine)
    cmdrsp.GetCommandResponder(snmp_engine, snmp_context)
    snmp_context.get_mib_instrum = lambda ctxName=b"": DynamicMibController()

    logging.info("SNMPv3 Server listening on UDP 0.0.0.0:10161...")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    import uvicorn

    # Run SNMP agent + FastAPI in the same event loop
    uvicorn_cfg = uvicorn.Config(api, host="0.0.0.0", port=8000, log_config=None)
    server = uvicorn.Server(uvicorn_cfg)

    async def run_all():
        await asyncio.gather(main(), server.serve())

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logging.info("Shutting down.")