import os
import csv
import glob
import logging
import asyncio
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.proto.api import v2c
from pysnmp.smi import instrum, exval

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

def load_most_recent_file(folder):
    csv_files = glob.glob(os.path.join(folder, "*.csv"))
    if csv_files:
        latest_file = max(csv_files, key=os.path.getmtime)
        parse_csv_file(latest_file)

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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutting down SNMP Server.")