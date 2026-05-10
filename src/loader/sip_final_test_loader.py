import os
import re
import shutil
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, date
import subprocess
import duckdb
import pandas as pd
import configparser

# =========================================================
# PORTFOLIO / EXTERNAL NAMING NOTE
# =========================================================
# This version uses anonymized external labels for documentation/GitHub:
# - Customer/product group: XU
# - Special device family: QX
# - System/project label: SiP
#
# Keep credentials, raw TXT files, DuckDB databases, CSV exports, and logs
# out of GitHub. Use a local config file only.

# =========================================================
# CONFIG
# =========================================================
SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "sip_loader_config.ini"

if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")


def load_config(config_path: Path) -> configparser.ConfigParser:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    cfg = configparser.ConfigParser()
    cfg.read(config_path, encoding="utf-8")
    return cfg


def expand_user_tokens(path_str: str, user_id: str) -> Path:
    return Path(path_str.replace("{USER_ID}", user_id))


CFG = load_config(CONFIG_PATH)
print(f"Using config file: {CONFIG_PATH}")

RUN_DT = datetime.now()
RUN_DATE_STR = RUN_DT.strftime("%Y%m%d")
YDAY = RUN_DT.date() - timedelta(days=1)
YDAY_YYYYMMDD = YDAY.strftime("%Y%m%d")

try:
    USER_ID = os.environ["USERNAME"]
except KeyError:
    raise RuntimeError("Windows environment variable 'USERNAME' not found.")

# -------------------------
# Local helper files
# -------------------------
FTP_SCRIPT_PATH = Path(f"D:/ASEKH/{USER_ID}/ftp_script.txt")
FTP_BATCH_PATH = Path(f"D:/ASEKH/{USER_ID}/run_ftp.bat")

# -------------------------
# Paths from config
# -------------------------
DOWNLOAD_DIR = expand_user_tokens(CFG["PATHS"]["download_dir"], USER_ID)
SOURCE_DIR = DOWNLOAD_DIR

LOCAL_BASE_DIR = expand_user_tokens(CFG["PATHS"]["local_base_dir"], USER_ID)
LOCAL_DB_PATH = LOCAL_BASE_DIR / CFG["OUTPUT"]["local_db_name"]
DUCKDB_TEMP_DIR = LOCAL_BASE_DIR / CFG["OUTPUT"]["duckdb_temp_subdir"]

# -------------------------
# Shared directories
# -------------------------
SHARED_ROOT = expand_user_tokens(CFG["PATHS"]["shared_root"], USER_ID)
OVERALL_DIR = SHARED_ROOT / CFG["OUTPUT"]["overall_subdir"]
SHARED_DB_DIR = SHARED_ROOT / CFG["OUTPUT"]["shared_db_subdir"]

# -------------------------
# Local logs
# -------------------------
LOG_SUBDIR = CFG["OUTPUT"].get("log_subdir", "logs")
LOG_DIR = LOCAL_BASE_DIR / LOG_SUBDIR
RUN_LOG_PATH = LOG_DIR / "sip_loader_run.log"

# -------------------------
# Shared output files
# -------------------------
OVERALL_HEADER_CSV = OVERALL_DIR / f"SiP_FT_Yield_Report_{RUN_DATE_STR}_(Security C).csv"
OVERALL_DETAIL_CSV = OVERALL_DIR / f"SiP_csv_per2d_past_1month_{RUN_DATE_STR}.csv"
OVERALL_LOG_TXT = OVERALL_DIR / f"log_{RUN_DATE_STR}.txt"

OVERALL_DUCKDB_COPY = SHARED_DB_DIR / "sip_final_test_summary.duckdb"
SHARED_RUN_LOG_PATH = SHARED_DB_DIR / "sip_loader_run.log"

# -------------------------
# FTP config
# -------------------------
FTP_HOST = CFG["FTP"]["host"]
FTP_USER = CFG["FTP"]["user"]
FTP_PASSWORD = CFG["FTP"]["password"]
CUST_CODE = CFG["FTP"]["cust_code"]
YR_CODE = int(CFG["FTP"]["yr_code"])

print(f"Resolved DOWNLOAD_DIR        : {DOWNLOAD_DIR}")
print(f"Resolved LOCAL_BASE_DIR      : {LOCAL_BASE_DIR}")
print(f"Resolved LOCAL_DB_PATH       : {LOCAL_DB_PATH}")
print(f"Resolved OVERALL_DIR         : {OVERALL_DIR}")
print(f"Resolved SHARED_DB_DIR       : {SHARED_DB_DIR}")
print(f"Resolved RUN_LOG_PATH        : {RUN_LOG_PATH}")
print(f"Resolved OVERALL_DUCKDB_COPY : {OVERALL_DUCKDB_COPY}")
print(f"Resolved SHARED_RUN_LOG_PATH : {SHARED_RUN_LOG_PATH}")

# -------------------------
# DuckDB / table config
# -------------------------
TABLE_NAME = "file_header"
DETAIL_TABLE = "detail_2d_list"
AUDIT_TABLE = "loaded_files_audit"

DUCKDB_THREADS = 1
DUCKDB_MEMORY_LIMIT = "2GB"

SPECIAL_DEVICE_CODES = {"QX1", "QX2", "QX1-NPI", "QX2-NPI"}
VALID_STATIONS = {"1000", "1001", "1002", "1010", "1011", "1012", "1004"}

HEADER_KEYS = {
    "Customer": "customer",
    "Schedule No.": "schedule_no",
    "Device Type": "device_type",
    "Lot ID": "lot_id",
    "Station": "station",
    "Recipe": "recipe",
    "Series": "device_code",
    "Test Program": "test_program",
    "Handler Name": "handler_name",
    "Start Time": "start_time",
    "End Time": "end_time",
}

# =========================================================
# HELPERS
# =========================================================
def ensure_folder(folder_path: Path) -> None:
    folder_path.mkdir(parents=True, exist_ok=True)


def cleanup_old_files(folder: Path, patterns: list[str]) -> None:
    ensure_folder(folder)
    for pattern in patterns:
        for old_file in folder.glob(pattern):
            try:
                old_file.unlink()
            except Exception as e:
                print(f"Warning: unable to delete old file {old_file}: {e}")


def clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def compute_file_hash(file_path: Path) -> str:
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def clean_soft_bin(val) -> str:
    if val is None:
        return "UNKNOWN"

    val = str(val).strip()
    if not val:
        return "UNKNOWN"

    val = val.replace("\n", " ").replace("|", " ")
    val = re.sub(r"\s+", " ", val)

    first_token = val.split(" ")[0].strip()
    return first_token if first_token else "UNKNOWN"


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def get_1month_cutoff() -> datetime:
    return (pd.Timestamp.today().normalize() - pd.DateOffset(months=1)).to_pydatetime()


def extract_filename_datetime(txt_path: Path):
    m = re.search(r"_(\d{14})\.txt$", txt_path.name, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except Exception:
        return None


def discover_recent_text_files(source_dir: Path, cutoff_dt: datetime) -> list[Path]:
    if not source_dir.exists():
        return []

    recent_files = []

    for txt_path in source_dir.glob("*.txt"):
        file_dt = extract_filename_datetime(txt_path)
        if file_dt is None:
            file_dt = datetime.fromtimestamp(txt_path.stat().st_mtime)

        if file_dt >= cutoff_dt:
            recent_files.append(txt_path)

    return sorted(recent_files)


def is_valid_station(station: str | None) -> bool:
    station = clean_text(station)
    return bool(station and station in VALID_STATIONS)


def parse_header_block(txt_path: Path) -> dict:
    result = {v: None for v in HEADER_KEYS.values()}

    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            raw = line.rstrip("\n")

            if raw.strip().startswith("===="):
                break

            if ":" not in raw:
                continue

            key, value = raw.split(":", 1)
            key = key.replace("\ufeff", "")
            key = re.sub(r"\s+", " ", key).strip()
            value = clean_text(value)

            if key in HEADER_KEYS:
                result[HEADER_KEYS[key]] = value

    result["station"] = clean_text(result["station"])
    result["device_code"] = clean_text(result["device_code"])
    result["recipe"] = clean_text(result["recipe"])
    result["device_type"] = clean_text(result["device_type"])
    result["start_time"] = parse_datetime(result["start_time"])
    result["end_time"] = parse_datetime(result["end_time"])
    result["source_modified_time"] = datetime.fromtimestamp(txt_path.stat().st_mtime)
    result["file_hash"] = compute_file_hash(txt_path)

    # -------------------------------------------------
    # NPI flagging fix for special QX devices
    # -------------------------------------------------
    recipe_upper = (result.get("recipe") or "").strip().upper()
    device_code_upper = (result.get("device_code") or "").strip().upper()
    device_type_upper = (result.get("device_type") or "").strip().upper()

    if "NPI" in recipe_upper:
        if "QX1" in device_code_upper or "QX1" in device_type_upper:
            result["device_code"] = "QX1-NPI"
        elif "QX2" in device_code_upper or "QX2" in device_type_upper:
            result["device_code"] = "QX2-NPI"

    return result


def parse_2d_list_block(txt_path: Path, header_row: dict) -> pd.DataFrame:
    rows = []
    in_2d_section = False
    skipped_header = False

    handler_name = clean_text(header_row.get("handler_name"))
    schedule_no = header_row.get("schedule_no")
    device_code = clean_text(header_row.get("device_code"))
    file_hash = header_row.get("file_hash")
    device_code_upper = (device_code or "").strip().upper()

    is_special_device = device_code_upper in SPECIAL_DEVICE_CODES

    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")

            if "***** 2D List" in line:
                in_2d_section = True
                skipped_header = False
                continue

            if not in_2d_section:
                continue

            if not skipped_header:
                if "Flow" in line and "DateTime" in line:
                    skipped_header = True
                continue

            if not line.strip():
                continue

            if not re.match(r"^\s*(FT|RT1|RT2|Overall)\b", line):
                continue

            parts = re.split(r"\s{2,}", line.strip())

            if is_special_device:
                if len(parts) < 8:
                    continue

                flow = clean_text(parts[0])
                long_token = clean_text(parts[1])
                site = clean_text(parts[2])
                tt = clean_text(parts[3])
                hb = clean_text(parts[4])
                sb = clean_text(parts[5])

                if len(parts) >= 9:
                    errCode = clean_text(parts[6])
                    pf_status = clean_text(parts[7])
                    dt_text = clean_text(parts[8])
                else:
                    errCode = None
                    pf_status = clean_text(parts[6])
                    dt_text = clean_text(parts[7])

                serial_no = long_token
                test_id = None

                if handler_name and long_token and long_token.endswith(handler_name):
                    serial_no = long_token[:-len(handler_name)]
                    test_id = handler_name

            else:
                if len(parts) < 10:
                    continue

                flow = clean_text(parts[0])
                serial_no = clean_text(parts[1])
                test_id = clean_text(parts[2])
                site = clean_text(parts[3])
                tt = clean_text(parts[4])
                hb = clean_text(parts[5])
                sb = clean_text(parts[6])
                errCode = clean_text(parts[7])
                pf_status = clean_text(parts[8])
                dt_text = clean_text(parts[9])

                if errCode is not None and errCode.strip().upper() == "PASS":
                    errCode = "Pass"

            test_dt = None
            if dt_text:
                try:
                    test_dt = datetime.strptime(dt_text, "%Y:%m:%d:%H:%M:%S")
                except Exception:
                    test_dt = None

            rows.append({
                "file_hash": file_hash,
                "schedule_no": schedule_no,
                "device_code": device_code,
                "handler_name": handler_name,
                "flow": flow,
                "serial_no": clean_text(serial_no),
                "test_id": clean_text(test_id),
                "site": clean_text(site),
                "test_time_sec": pd.to_numeric(tt, errors="coerce"),
                "hb": clean_text(hb),
                "sb": clean_text(sb),
                "errCode": clean_text(errCode),
                "pf_status": clean_text(pf_status),
                "test_datetime": test_dt,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "file_hash", "schedule_no", "device_code", "handler_name",
            "flow", "serial_no", "test_id", "site", "test_time_sec",
            "hb", "sb", "errCode", "pf_status", "test_datetime"
        ])

    df = pd.DataFrame(rows)
    df["sb"] = df["sb"].apply(clean_soft_bin)
    df["test_time_sec"] = pd.to_numeric(df["test_time_sec"], errors="coerce").astype("Int64")
    return df


def connect_duckdb(db_path: Path) -> duckdb.DuckDBPyConnection:
    ensure_folder(db_path.parent)
    ensure_folder(DUCKDB_TEMP_DIR)

    conn = duckdb.connect(str(db_path))
    conn.execute(f"SET threads={DUCKDB_THREADS}")
    conn.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT}'")
    conn.execute(f"SET temp_directory='{DUCKDB_TEMP_DIR.as_posix()}'")

    return conn


def clear_duckdb_temp_folder(folder: Path) -> None:
    ensure_folder(folder)
    for item in folder.iterdir():
        try:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as e:
            print(f"Warning: unable to delete temp item {item}: {e}")


def table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    df = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM information_schema.tables
        WHERE table_name = ?
    """, [table_name]).fetchdf()
    return int(df.loc[0, "cnt"]) > 0


def get_table_columns(conn: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    df = conn.execute(f"PRAGMA table_info('{table_name}')").fetchdf()
    return set(df["name"].astype(str).tolist())


def migrate_legacy_schema(conn: duckdb.DuckDBPyConnection) -> None:
    for table_name in [TABLE_NAME, DETAIL_TABLE]:
        cols = get_table_columns(conn, table_name)
        if "series" in cols and "device_code" not in cols:
            conn.execute(f'ALTER TABLE {table_name} RENAME COLUMN "series" TO "device_code"')


def drop_unused_header_columns(conn: duckdb.DuckDBPyConnection) -> None:
    cols = get_table_columns(conn, TABLE_NAME)

    if "part_number" in cols:
        conn.execute(f'ALTER TABLE {TABLE_NAME} DROP COLUMN part_number')

    if "config_file" in cols:
        conn.execute(f'ALTER TABLE {TABLE_NAME} DROP COLUMN config_file')


def daterange(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def create_ftp_script_u7(
    start_date: date,
    end_date: date,
    cust_code: str,
    yr_code: int,
    ftp_script_path: Path,
    download_dir: Path
) -> Path:
    ensure_folder(download_dir)
    ensure_folder(ftp_script_path.parent)

    yr_code_bef = yr_code - 1
    if yr_code_bef < 0:
        yr_code_bef = 9

    target_stations = ["1000", "1001", "1002", "1010", "1011", "1012", "1004"]

    lines = [
        f"open {FTP_HOST}",
        FTP_USER,
        FTP_PASSWORD,
        f"cd /SUM/{cust_code}/{yr_code}",
        "binary",
        "prompt",
        f"lcd {download_dir}",
    ]

    for d in daterange(start_date, end_date):
        ymd = d.strftime("%Y%m%d")
        for station in target_stations:
            lines.append(f"mget ??????????_{station}_*{ymd}??????.txt")

    lines.append(f"cd /SUM/{cust_code}/{yr_code_bef}")

    for d in daterange(start_date, end_date):
        ymd = d.strftime("%Y%m%d")
        for station in target_stations:
            lines.append(f"mget ??????????_{station}_*{ymd}??????.txt")

    lines.extend([
        "Disconnect",
        "bye"
    ])

    script_text = "\n".join(lines) + "\n"

    if ftp_script_path.exists():
        try:
            ftp_script_path.unlink()
        except PermissionError:
            ftp_script_path = ftp_script_path.with_name(
                f"{ftp_script_path.stem}_{datetime.now().strftime('%H%M%S')}{ftp_script_path.suffix}"
            )

    ftp_script_path.write_text(script_text, encoding="utf-8")
    return ftp_script_path


def get_txt_files_snapshot(folder: Path) -> set[str]:
    ensure_folder(folder)
    return {p.name for p in folder.glob("*.txt")}


def download_previous_day_txt_files():
    start_date = YDAY
    end_date = YDAY

    before_files = get_txt_files_snapshot(DOWNLOAD_DIR)

    ftp_script = create_ftp_script_u7(
        start_date=start_date,
        end_date=end_date,
        cust_code=CUST_CODE,
        yr_code=YR_CODE,
        ftp_script_path=FTP_SCRIPT_PATH,
        download_dir=DOWNLOAD_DIR
    )

    print(f"FTP script created: {ftp_script}")

    batch_path = Path(f"D:/ASEKH/{USER_ID}/run_ftp.bat")

    create_batch_file(
        batch_path=batch_path,
        ftp_script_path=ftp_script,
        working_dir=DOWNLOAD_DIR
    )

    print(f"Batch file created: {batch_path}")

    run_batch_file(batch_path)

    after_files = get_txt_files_snapshot(DOWNLOAD_DIR)
    new_files = sorted(after_files - before_files)

    print(f"New txt files downloaded: {len(new_files)}")
    if new_files:
        for name in new_files[:20]:
            print(name)
        if len(new_files) > 20:
            print(f"... and {len(new_files) - 20} more")


def create_batch_file(
    batch_path: Path,
    ftp_script_path: Path,
    working_dir: Path
) -> Path:
    ensure_folder(batch_path.parent)

    lines = [
        "@echo off",
        "echo ========================================",
        "echo SiP TXT FTP DOWNLOAD STARTED",
        "echo ========================================",
        "echo Working Dir: " + str(working_dir),
        "echo FTP Script : " + str(ftp_script_path),
        "echo.",
        f"cd /d {working_dir}",
        "echo Running FTP download...",
        f"ftp -s:{ftp_script_path}",
        "echo.",
        "echo FTP download finished.",
        "echo ========================================",
        "echo."
    ]

    batch_path.write_text("\r\n".join(lines) + "\r\n", encoding="mbcs")
    return batch_path


def run_batch_file(batch_path: Path, log_func=None):
    if not batch_path.exists():
        raise FileNotFoundError(f"Batch file not found: {batch_path}")

    process = subprocess.Popen(
        [str(batch_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True
    )

    print("\n=== FTP DOWNLOAD LOG ===")
    if log_func:
        log_func("=== FTP DOWNLOAD LOG ===")

    for line in process.stdout:
        line = line.rstrip()
        if line:
            print(line, flush=True)
            if log_func:
                log_func(line)

    process.wait()

    if process.returncode != 0:
        raise RuntimeError(f"Batch execution failed: {process.returncode}")


def remove_unwanted_txt_files(folder: Path, keywords: list[str]) -> int:
    ensure_folder(folder)

    deleted_count = 0
    upper_keywords = [k.upper() for k in keywords]

    for txt_file in folder.glob("*.txt"):
        fname_upper = txt_file.name.upper()
        if any(k in fname_upper for k in upper_keywords):
            try:
                txt_file.unlink()
                deleted_count += 1
                print(f"Deleted unwanted txt: {txt_file.name}")
            except Exception as e:
                print(f"Warning: unable to delete {txt_file.name}: {e}")

    return deleted_count


def is_valid_customer(customer: str | None) -> bool:
    customer = clean_text(customer)
    return bool(customer and customer.upper() == "XU")


def clear_raw_data_folder(folder: Path) -> int:
    """
    Deletes all files inside raw_data folder safely.
    Returns number of files deleted.
    """
    ensure_folder(folder)

    deleted_count = 0

    for item in folder.iterdir():
        try:
            if item.is_file():
                item.unlink()
                deleted_count += 1
            elif item.is_dir():
                shutil.rmtree(item)
                deleted_count += 1
        except Exception as e:
            print(f"Warning: unable to delete {item}: {e}")

    return deleted_count


def validate_raw_data_path(folder: Path):
    folder_str = str(folder).replace("\\", "/").lower()

    if "download_sip/raw_data" not in folder_str:
        raise RuntimeError(f"SAFETY STOP: Refusing to clear unexpected path: {folder}")

# =========================================================
# SIMPLE RUN LOGGER
# =========================================================
def init_run_log(log_path: Path) -> None:
    log_path = Path(log_path)
    ensure_folder(log_path.parent)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


def write_run_log(log_path: Path, message: str) -> None:
    log_path = Path(log_path)
    ensure_folder(log_path.parent)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {message}\n")


def init_database_objects(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            file_hash VARCHAR PRIMARY KEY,
            source_modified_time TIMESTAMP,

            customer VARCHAR,
            schedule_no VARCHAR,
            device_type VARCHAR,
            lot_id VARCHAR,
            station VARCHAR,
            recipe VARCHAR,
            device_code VARCHAR,
            test_program VARCHAR,
            handler_name VARCHAR,
            start_time TIMESTAMP,
            end_time TIMESTAMP,

            input_quantity BIGINT,
            first_pass_qty BIGINT,
            first_pass_yield_pct DOUBLE,
            final_pass_qty BIGINT,
            final_yield_pct DOUBLE,
            retest_pass_qty BIGINT,
            retest_pass_yield_pct DOUBLE,
            retest_rate_pct DOUBLE,

            loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {DETAIL_TABLE} (
            file_hash VARCHAR,
            schedule_no VARCHAR,
            device_code VARCHAR,
            handler_name VARCHAR,

            flow VARCHAR,
            serial_no VARCHAR,
            test_id VARCHAR,
            site VARCHAR,
            test_time_sec BIGINT,
            hb VARCHAR,
            sb VARCHAR,
            errCode VARCHAR,
            pf_status VARCHAR,
            test_datetime TIMESTAMP
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
            file_hash VARCHAR PRIMARY KEY,
            source_modified_time TIMESTAMP,
            load_status VARCHAR,
            loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    migrate_legacy_schema(conn)
    drop_unused_header_columns(conn)


def purge_invalid_scope(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(f"""
        DELETE FROM {DETAIL_TABLE}
        WHERE file_hash IN (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE COALESCE(TRIM(station), '') NOT IN ('1000','1001','1002','1010','1011','1012','1004')
        )
    """)

    conn.execute(f"""
        DELETE FROM {AUDIT_TABLE}
        WHERE file_hash IN (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE COALESCE(TRIM(station), '') NOT IN ('1000','1001','1002','1010','1011','1012','1004')
        )
    """)

    conn.execute(f"""
        DELETE FROM {TABLE_NAME}
        WHERE COALESCE(TRIM(station), '') NOT IN ('1000','1001','1002','1010','1011','1012','1004')
    """)


def purge_non_scoped_rows(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Purge any rows that are outside the XU production scope.
    This ensures the DB only contains valid scoped device data.
    """

    conn.execute(f"""
        DELETE FROM {DETAIL_TABLE}
        WHERE file_hash IN (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE
                UPPER(TRIM(customer)) <> 'XU'
                OR UPPER(COALESCE(TRIM(device_code), '')) LIKE 'MDX%'
        )
    """)

    conn.execute(f"""
        DELETE FROM {AUDIT_TABLE}
        WHERE file_hash IN (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE
                UPPER(TRIM(customer)) <> 'XU'
                OR UPPER(COALESCE(TRIM(device_code), '')) LIKE 'MDX%'
        )
    """)

    conn.execute(f"""
        DELETE FROM {TABLE_NAME}
        WHERE
            UPPER(TRIM(customer)) <> 'XU'
            OR UPPER(COALESCE(TRIM(device_code), '')) LIKE 'MDX%'
    """)


def is_fresh_file(
    conn: duckdb.DuckDBPyConnection,
    file_hash: str
) -> bool:
    df = conn.execute(f"""
        SELECT file_hash, load_status
        FROM {AUDIT_TABLE}
        WHERE file_hash = ?
    """, [file_hash]).fetchdf()

    if df.empty:
        return True

    old_status = df.loc[0, "load_status"]
    if old_status != "SUCCESS":
        return True

    return False


def upsert_header_row(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    conn.execute(f"DELETE FROM {TABLE_NAME} WHERE file_hash = ?", [row["file_hash"]])

    df = pd.DataFrame([row])
    conn.register("stg_header", df)

    conn.execute(f"""
        INSERT INTO {TABLE_NAME} BY NAME
        SELECT * FROM stg_header
    """)
    conn.unregister("stg_header")


def replace_detail_rows_for_file(conn: duckdb.DuckDBPyConnection, detail_df: pd.DataFrame, file_hash: str) -> None:
    conn.execute(f"DELETE FROM {DETAIL_TABLE} WHERE file_hash = ?", [file_hash])

    if detail_df.empty:
        return

    conn.register("stg_detail", detail_df)
    conn.execute(f"""
        INSERT INTO {DETAIL_TABLE} BY NAME
        SELECT * FROM stg_detail
    """)
    conn.unregister("stg_detail")


def update_audit_success(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    conn.execute(f"""
        INSERT OR REPLACE INTO {AUDIT_TABLE} (
            file_hash,
            source_modified_time,
            load_status,
            loaded_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, [
        row["file_hash"],
        row["source_modified_time"],
        "SUCCESS"
    ])


def update_audit_failed(conn: duckdb.DuckDBPyConnection, txt_path: Path, err_msg: str) -> None:
    conn.execute(f"""
        INSERT OR REPLACE INTO {AUDIT_TABLE} (
            file_hash,
            source_modified_time,
            load_status,
            loaded_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, [
        compute_file_hash(txt_path),
        datetime.fromtimestamp(txt_path.stat().st_mtime),
        f"FAILED: {err_msg[:200]}"
    ])


def update_header_metrics_for_files(conn: duckdb.DuckDBPyConnection, file_hashes: list[str]) -> None:
    if not file_hashes:
        print("No files to update metrics for.", flush=True)
        return

    file_hashes = sorted(set(file_hashes))
    hashes_df = pd.DataFrame({"file_hash": file_hashes})
    conn.register("stg_metric_files", hashes_df)

    conn.execute(f"""
        UPDATE {TABLE_NAME} AS h
        SET
            input_quantity = m.input_quantity,
            first_pass_qty = m.first_pass_qty,
            first_pass_yield_pct = m.first_pass_yield_pct,
            final_pass_qty = m.final_pass_qty,
            final_yield_pct = m.final_yield_pct,
            retest_pass_qty = m.retest_pass_qty,
            retest_pass_yield_pct = m.retest_pass_yield_pct,
            retest_rate_pct = m.retest_rate_pct
        FROM (
            WITH target_files AS (
                SELECT file_hash
                FROM stg_metric_files
            ),
            target_detail AS (
                SELECT d.*
                FROM {DETAIL_TABLE} d
                INNER JOIN target_files t
                    ON d.file_hash = t.file_hash
            ),
            input_counts AS (
                SELECT
                    file_hash,
                    COUNT(DISTINCT serial_no) AS input_quantity
                FROM target_detail
                GROUP BY file_hash
            ),
            first_ft AS (
                SELECT
                    file_hash,
                    serial_no,
                    pf_status AS first_ft_status
                FROM target_detail
                WHERE flow = 'FT'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY file_hash, serial_no
                    ORDER BY test_datetime ASC
                ) = 1
            ),
            first_ft_counts AS (
                SELECT
                    file_hash,
                    COUNT(DISTINCT CASE WHEN first_ft_status = 'PASS' THEN serial_no END) AS first_pass_qty
                FROM first_ft
                GROUP BY file_hash
            ),
            latest_all AS (
                SELECT
                    file_hash,
                    serial_no,
                    pf_status AS latest_status
                FROM target_detail
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY file_hash, serial_no
                    ORDER BY test_datetime DESC
                ) = 1
            ),
            final_pass_counts AS (
                SELECT
                    file_hash,
                    COUNT(DISTINCT serial_no) AS final_pass_qty
                FROM latest_all
                WHERE latest_status = 'PASS'
                GROUP BY file_hash
            ),
            retest_pass_counts AS (
                SELECT
                    file_hash,
                    COUNT(DISTINCT serial_no) AS retest_pass_qty
                FROM target_detail
                WHERE flow IN ('RT1', 'RT2')
                  AND pf_status = 'PASS'
                GROUP BY file_hash
            ),
            retest_seen_counts AS (
                SELECT
                    file_hash,
                    COUNT(DISTINCT serial_no) AS retest_seen_qty
                FROM target_detail
                WHERE flow IN ('RT1', 'RT2')
                GROUP BY file_hash
            )
            SELECT
                i.file_hash,
                i.input_quantity,
                COALESCE(f.first_pass_qty, 0) AS first_pass_qty,
                ROUND(100.0 * COALESCE(f.first_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS first_pass_yield_pct,
                COALESCE(fin.final_pass_qty, 0) AS final_pass_qty,
                ROUND(100.0 * COALESCE(fin.final_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS final_yield_pct,
                COALESCE(rp.retest_pass_qty, 0) AS retest_pass_qty,
                ROUND(100.0 * COALESCE(rp.retest_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS retest_pass_yield_pct,
                ROUND(100.0 * COALESCE(rs.retest_seen_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS retest_rate_pct
            FROM input_counts i
            LEFT JOIN first_ft_counts f
                ON i.file_hash = f.file_hash
            LEFT JOIN final_pass_counts fin
                ON i.file_hash = fin.file_hash
            LEFT JOIN retest_pass_counts rp
                ON i.file_hash = rp.file_hash
            LEFT JOIN retest_seen_counts rs
                ON i.file_hash = rs.file_hash
        ) AS m
        WHERE h.file_hash = m.file_hash
    """)

    conn.unregister("stg_metric_files")


def get_softbin_fail_pivot_df(conn: duckdb.DuckDBPyConnection, cutoff_date=None) -> pd.DataFrame:
    params = []
    where_sql = ""

    if cutoff_date is not None:
        where_sql = "WHERE CAST(h.end_time AS DATE) >= ?"
        params.append(pd.to_datetime(cutoff_date).date())

    softbin_df = conn.execute(f"""
        WITH recent_files AS (
            SELECT h.file_hash, h.device_code, h.input_quantity, h.end_time
            FROM {TABLE_NAME} h
            {where_sql}
        ),
        latest_per_serial AS (
            SELECT
                d.file_hash,
                r.device_code,
                d.serial_no,
                SPLIT_PART(
                    REPLACE(REPLACE(COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN'), '\n', ' '), '|', ' '),
                    ' ',
                    1
                ) AS soft_bin,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN recent_files r
                ON d.file_hash = r.file_hash
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.file_hash, d.serial_no
                ORDER BY d.test_datetime DESC
            ) = 1
        ),
        fail_softbins AS (
            SELECT
                l.file_hash,
                l.device_code,
                l.soft_bin,
                COUNT(*) AS fail_qty
            FROM latest_per_serial l
            WHERE l.pf_status = 'FAIL'
            GROUP BY l.file_hash, l.device_code, l.soft_bin
        )
        SELECT
            f.file_hash,
            f.device_code,
            f.soft_bin,
            f.fail_qty
        FROM fail_softbins f
    """, params).fetchdf()

    if softbin_df.empty:
        return pd.DataFrame(columns=["file_hash"])

    softbin_df["soft_bin"] = softbin_df["soft_bin"].astype(str)

    softbin_order = (
        softbin_df.groupby("soft_bin", as_index=False)["fail_qty"]
        .sum()
        .sort_values(["fail_qty", "soft_bin"], ascending=[False, True])["soft_bin"]
        .tolist()
    )

    qty_pivot = softbin_df.pivot_table(
        index="file_hash",
        columns="soft_bin",
        values="fail_qty",
        aggfunc="sum",
        fill_value=0
    ).reindex(columns=softbin_order, fill_value=0)

    qty_pivot.columns = [f"{col}(qty)" for col in qty_pivot.columns]
    qty_pivot = qty_pivot.astype(int)

    return qty_pivot.reset_index()


def write_csv_safely(df: pd.DataFrame, csv_path: Path) -> Path:
    ensure_folder(csv_path.parent)
    temp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")

    try:
        if temp_path.exists():
            temp_path.unlink()

        df.to_csv(temp_path, index=False, encoding="utf-8-sig")
        temp_path.replace(csv_path)
        return csv_path

    except PermissionError:
        fallback_path = csv_path.with_name(
            f"{csv_path.stem}_{datetime.now().strftime('%H%M%S')}{csv_path.suffix}"
        )
        df.to_csv(fallback_path, index=False, encoding="utf-8-sig")
        print(f"Main CSV locked. Saved fallback file instead: {fallback_path}")
        return fallback_path


def copy_file_overwrite(src: Path, dst: Path) -> None:
    ensure_folder(dst.parent)

    if dst.exists():
        try:
            dst.unlink()
        except Exception:
            pass

    shutil.copy2(src, dst)


def purge_non_XU_rows(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(f"""
        DELETE FROM {DETAIL_TABLE}
        WHERE file_hash IN (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE UPPER(COALESCE(TRIM(customer), '')) <> 'XU'
        )
    """)

    conn.execute(f"""
        DELETE FROM {AUDIT_TABLE}
        WHERE file_hash IN (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE UPPER(COALESCE(TRIM(customer), '')) <> 'XU'
        )
    """)

    conn.execute(f"""
        DELETE FROM {TABLE_NAME}
        WHERE UPPER(COALESCE(TRIM(customer), '')) <> 'XU'
    """)


def dedupe_latest_lot_versions(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Production dedupe logic for duplicate uploads.

    Old behavior partitioned by CAST(end_time AS DATE), which allowed the same lot to be
    reported again when a completed lot was re-uploaded after midnight.

    Updated rule:
    - Same lot_id + device_code + station + schedule_no + start_time = duplicate upload
    - Keep earliest end_time so the original completed report remains on the correct day
    - True cross-midnight lots are preserved because they should only have one start_time record
    """
    duplicates_df = conn.execute(f"""
        WITH ranked AS (
            SELECT
                file_hash,
                lot_id,
                device_code,
                station,
                schedule_no,
                start_time,
                end_time,
                CAST(end_time AS DATE) AS end_date,
                source_modified_time,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(TRIM(lot_id), ''),
                        COALESCE(TRIM(device_code), ''),
                        COALESCE(TRIM(station), ''),
                        COALESCE(TRIM(schedule_no), ''),
                        start_time
                    ORDER BY
                        end_time ASC NULLS LAST,
                        source_modified_time ASC NULLS LAST,
                        file_hash ASC
                ) AS rn
            FROM {TABLE_NAME}
            WHERE COALESCE(TRIM(lot_id), '') <> ''
        )
        SELECT
            file_hash,
            lot_id,
            device_code,
            station,
            schedule_no,
            start_time,
            end_time
        FROM ranked
        WHERE rn > 1
        ORDER BY lot_id, device_code, station, schedule_no, start_time, end_time
    """).fetchdf()

    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE keep_latest_files AS
        WITH ranked AS (
            SELECT
                file_hash,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(TRIM(lot_id), ''),
                        COALESCE(TRIM(device_code), ''),
                        COALESCE(TRIM(station), ''),
                        COALESCE(TRIM(schedule_no), ''),
                        start_time
                    ORDER BY
                        end_time ASC NULLS LAST,
                        source_modified_time ASC NULLS LAST,
                        file_hash ASC
                ) AS rn
            FROM {TABLE_NAME}
            WHERE COALESCE(TRIM(lot_id), '') <> ''
        )
        SELECT file_hash
        FROM ranked
        WHERE rn = 1

        UNION

        SELECT file_hash
        FROM {TABLE_NAME}
        WHERE COALESCE(TRIM(lot_id), '') = ''
    """)

    conn.execute(f"""
        DELETE FROM {DETAIL_TABLE}
        WHERE file_hash NOT IN (
            SELECT file_hash FROM keep_latest_files
        )
    """)

    conn.execute(f"""
        DELETE FROM {TABLE_NAME}
        WHERE file_hash NOT IN (
            SELECT file_hash FROM keep_latest_files
        )
    """)

    conn.execute(f"""
        DELETE FROM {AUDIT_TABLE}
        WHERE file_hash NOT IN (
            SELECT file_hash FROM keep_latest_files
        )
    """)

    return duplicates_df


def build_header_export_df(
    conn: duckdb.DuckDBPyConnection,
    filter_date=None,
    cutoff_date=None
) -> pd.DataFrame:
    params = []
    where_clauses = []

    if cutoff_date is not None:
        where_clauses.append("CAST(end_time AS DATE) >= ?")
        params.append(pd.to_datetime(cutoff_date).date())

    if filter_date is not None:
        where_clauses.append("CAST(end_time AS DATE) = ?")
        params.append(pd.to_datetime(filter_date).date())

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    header_df = conn.execute(f"""
        SELECT
            file_hash,
            customer,
            schedule_no,
            device_type,
            lot_id,
            station,
            recipe,
            device_code,
            test_program,
            handler_name,
            start_time,
            CAST(start_time AS DATE) AS start_date,
            end_time,
            CAST(end_time AS DATE) AS end_date,
            input_quantity,
            first_pass_qty,
            first_pass_yield_pct,
            final_pass_qty,
            final_yield_pct,
            retest_pass_qty,
            retest_pass_yield_pct,
            retest_rate_pct
        FROM {TABLE_NAME}
        {where_sql}
        ORDER BY end_time, file_hash
    """, params).fetchdf()

    if header_df.empty:
        return header_df

    header_df["start_date"] = pd.to_datetime(header_df["start_date"], errors="coerce").dt.date
    header_df["end_date"] = pd.to_datetime(header_df["end_date"], errors="coerce").dt.date

    softbin_cutoff = cutoff_date if cutoff_date is not None else filter_date
    softbin_pivot_df = get_softbin_fail_pivot_df(conn, cutoff_date=softbin_cutoff)

    if not softbin_pivot_df.empty:
        header_df = header_df.merge(softbin_pivot_df, on="file_hash", how="left")

    dynamic_cols = [c for c in header_df.columns if c.endswith("(qty)")]

    for col in dynamic_cols:
        header_df[col] = header_df[col].fillna(0).astype(int)

    fixed_cols = [
        "customer",
        "schedule_no",
        "device_type",
        "lot_id",
        "station",
        "device_code",
        "test_program",
        "handler_name",
        "start_time",
        "start_date",
        "end_time",
        "input_quantity",
        "first_pass_qty",
        "first_pass_yield_pct",
        "final_pass_qty",
        "final_yield_pct",
        "retest_pass_qty",
        "retest_pass_yield_pct",
        "retest_rate_pct",
    ]

    ordered_dynamic_cols = []
    if dynamic_cols:
        seen_softbins = []
        for col in dynamic_cols:
            sb = col.rsplit("(", 1)[0]
            if sb not in seen_softbins:
                seen_softbins.append(sb)

        for sb in seen_softbins:
            qty_col = f"{sb}(qty)"
            if qty_col in header_df.columns:
                ordered_dynamic_cols.append(qty_col)

    final_cols = fixed_cols + ordered_dynamic_cols
    final_cols = [c for c in final_cols if c in header_df.columns]

    export_df = header_df[final_cols].copy()
    export_df = export_df.sort_values(["end_time", "schedule_no", "lot_id"], na_position="last")
    export_df.reset_index(drop=True, inplace=True)

    return export_df


def build_daily_24h_export_df(
    conn: duckdb.DuckDBPyConnection,
    day_start: datetime,
    day_end: datetime
) -> pd.DataFrame:
    header_df = conn.execute(f"""
        SELECT
            file_hash,
            customer,
            schedule_no,
            device_type,
            lot_id,
            station,
            recipe,
            device_code,
            test_program,
            handler_name,
            start_time,
            CAST(start_time AS DATE) AS start_date,
            end_time,
            CAST(end_time AS DATE) AS end_date,
            input_quantity,
            first_pass_qty,
            first_pass_yield_pct,
            final_pass_qty,
            final_yield_pct,
            retest_pass_qty,
            retest_pass_yield_pct,
            retest_rate_pct
        FROM {TABLE_NAME}
        WHERE end_time >= ?
          AND end_time < ?
          AND station <> '1004'
          AND UPPER(TRIM(customer)) = 'XU'
          AND UPPER(COALESCE(TRIM(device_code), '')) NOT LIKE 'MDX%'
        ORDER BY end_time, file_hash
    """, [day_start, day_end]).fetchdf()

    if header_df.empty:
        return header_df

    header_df["start_date"] = pd.to_datetime(header_df["start_date"], errors="coerce").dt.date
    header_df["end_date"] = pd.to_datetime(header_df["end_date"], errors="coerce").dt.date

    softbin_df = conn.execute(f"""
        WITH daily_files AS (
            SELECT file_hash
            FROM {TABLE_NAME}
            WHERE end_time >= ?
              AND end_time < ?
              AND station <> '1004'
              AND UPPER(TRIM(customer)) = 'XU'
              AND UPPER(COALESCE(TRIM(device_code), '')) NOT LIKE 'MDX%'
        ),
        latest_per_serial AS (
            SELECT
                d.file_hash,
                d.serial_no,
                SPLIT_PART(
                    REPLACE(REPLACE(COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN'), '\n', ' '), '|', ' '),
                    ' ',
                    1
                ) AS soft_bin,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN daily_files f
                ON d.file_hash = f.file_hash
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.file_hash, d.serial_no
                ORDER BY d.test_datetime DESC
            ) = 1
        ),
        fail_softbins AS (
            SELECT
                file_hash,
                soft_bin,
                COUNT(*) AS fail_qty
            FROM latest_per_serial
            WHERE pf_status = 'FAIL'
            GROUP BY file_hash, soft_bin
        )
        SELECT *
        FROM fail_softbins
    """, [day_start, day_end]).fetchdf()

    if not softbin_df.empty:
        softbin_order = (
            softbin_df.groupby("soft_bin", as_index=False)["fail_qty"]
            .sum()
            .sort_values(["fail_qty", "soft_bin"], ascending=[False, True])["soft_bin"]
            .tolist()
        )

        qty_pivot = softbin_df.pivot_table(
            index="file_hash",
            columns="soft_bin",
            values="fail_qty",
            aggfunc="sum",
            fill_value=0
        ).reindex(columns=softbin_order, fill_value=0)

        qty_pivot.columns = [f"{col}(qty)" for col in qty_pivot.columns]
        qty_pivot = qty_pivot.astype(int).reset_index()

        header_df = header_df.merge(qty_pivot, on="file_hash", how="left")

    dynamic_cols = [c for c in header_df.columns if c.endswith("(qty)")]
    for col in dynamic_cols:
        header_df[col] = header_df[col].fillna(0).astype(int)

    fixed_cols = [
        "customer",
        "schedule_no",
        "device_type",
        "lot_id",
        "station",
        "device_code",
        "test_program",
        "handler_name",
        "start_time",
        "start_date",
        "end_time",
        "input_quantity",
        "first_pass_qty",
        "first_pass_yield_pct",
        "final_pass_qty",
        "final_yield_pct",
        "retest_pass_qty",
        "retest_pass_yield_pct",
        "retest_rate_pct",
    ]

    final_cols = fixed_cols + dynamic_cols
    final_cols = [c for c in final_cols if c in header_df.columns]

    export_df = header_df[final_cols].copy()
    export_df = export_df.sort_values(["end_time", "schedule_no", "lot_id"], na_position="last")
    export_df.reset_index(drop=True, inplace=True)

    if not export_df.empty:
        print("Daily export customers:", sorted(export_df["customer"].astype(str).str.strip().str.upper().unique().tolist()))
        print("Daily export stations :", sorted(export_df["station"].astype(str).str.strip().unique().tolist()))
    else:
        print("Daily export dataframe is empty.")

    return export_df


def export_overall_header_csv(conn: duckdb.DuckDBPyConnection, csv_path: Path, cutoff_date) -> None:
    export_df = build_header_export_df(conn, cutoff_date=cutoff_date)
    write_csv_safely(export_df, csv_path)


def main():
    deleted_unwanted_count = 0
    txt_files = []

    ensure_folder(LOG_DIR)
    init_run_log(RUN_LOG_PATH)

    clear_duckdb_temp_folder(DUCKDB_TEMP_DIR)

    ensure_folder(SOURCE_DIR)
    ensure_folder(LOCAL_BASE_DIR)
    ensure_folder(OVERALL_DIR)
    ensure_folder(SHARED_DB_DIR)

    cleanup_old_files(OVERALL_DIR, [
        "SiP_FT_Yield_Report_*_(Security C).csv",
    ])

    write_run_log(RUN_LOG_PATH, "Clearing raw_data folder before download")
    validate_raw_data_path(SOURCE_DIR)
    deleted_raw_count = clear_raw_data_folder(SOURCE_DIR)
    write_run_log(RUN_LOG_PATH, f"Raw data files deleted: {deleted_raw_count}")

    write_run_log(RUN_LOG_PATH, "Starting TXT file download...")
    download_previous_day_txt_files()
    write_run_log(RUN_LOG_PATH, "TXT file download completed")

    deleted_unwanted_count = remove_unwanted_txt_files(
        SOURCE_DIR,
        ["CORR", "CAL"]
    )
    write_run_log(RUN_LOG_PATH, f"Unwanted txt files deleted: {deleted_unwanted_count}")

    cutoff_dt = get_1month_cutoff()

    write_run_log(RUN_LOG_PATH, f"SOURCE_DIR         : {SOURCE_DIR}")
    write_run_log(RUN_LOG_PATH, f"LOCAL_DB_PATH      : {LOCAL_DB_PATH}")
    write_run_log(RUN_LOG_PATH, f"CUSTOMER_HEADER_CSV: {OVERALL_HEADER_CSV}")
    write_run_log(RUN_LOG_PATH, f"1-MONTH CUTOFF     : {cutoff_dt:%Y-%m-%d %H:%M:%S}")

    txt_files = discover_recent_text_files(SOURCE_DIR, cutoff_dt)
    write_run_log(RUN_LOG_PATH, f"Recent TXT files found (last 1 month only): {len(txt_files)}")

    if not txt_files:
        write_run_log(RUN_LOG_PATH, "No recent .txt files found. Nothing to process.")
        copy_file_overwrite(RUN_LOG_PATH, SHARED_RUN_LOG_PATH)
        return

    conn = None
    skipped_non_XU_count = 0
    loaded_count = 0
    skipped_count = 0
    skipped_invalid_station_count = 0
    skipped_unchanged_count = 0
    skipped_MDX_count = 0
    duplicates_df = pd.DataFrame()
    touched_file_hashes = []

    try:
        write_run_log(RUN_LOG_PATH, "Connecting to DuckDB")
        conn = connect_duckdb(LOCAL_DB_PATH)

        write_run_log(RUN_LOG_PATH, "Initializing database objects")
        init_database_objects(conn)

        write_run_log(RUN_LOG_PATH, "Purging invalid scope records")
        purge_invalid_scope(conn)

        write_run_log(RUN_LOG_PATH, "Purging non-scoped customer rows")
        purge_non_XU_rows(conn)

        write_run_log(RUN_LOG_PATH, "Purging non-scoped rows (scoped-customer enforcement)")
        purge_non_scoped_rows(conn)

        for i, txt_path in enumerate(txt_files, start=1):
            try:
                write_run_log(RUN_LOG_PATH, f"[{i}/{len(txt_files)}] Checking {txt_path.name}")

                row = parse_header_block(txt_path)
                station_val = clean_text(row.get("station"))
                device_code_val = (clean_text(row.get("device_code")) or "").upper()
                customer_val = (clean_text(row.get("customer")) or "").upper()

                if not is_valid_customer(customer_val):
                    skipped_count += 1
                    skipped_non_XU_count += 1
                    write_run_log(
                        RUN_LOG_PATH,
                        f"Skipped non-scoped customer file: {txt_path.name} | customer={row.get('customer')}"
                    )
                    continue

                if not is_valid_station(station_val):
                    skipped_count += 1
                    skipped_invalid_station_count += 1
                    write_run_log(
                        RUN_LOG_PATH,
                        f"Skipped invalid station: {txt_path.name} | station={station_val}"
                    )
                    continue

                file_dt = row.get("start_time") or row.get("end_time") or row.get("source_modified_time")
                if file_dt is not None and file_dt < cutoff_dt:
                    skipped_count += 1
                    write_run_log(
                        RUN_LOG_PATH,
                        f"Skipped old file outside 1-month cutoff: {txt_path.name}"
                    )
                    continue

                fresh = is_fresh_file(
                    conn=conn,
                    file_hash=row["file_hash"]
                )

                if not fresh:
                    skipped_count += 1
                    skipped_unchanged_count += 1
                    write_run_log(RUN_LOG_PATH, f"Skipped unchanged file: {txt_path.name}")
                    continue

                upsert_header_row(conn, row)

                detail_df = parse_2d_list_block(txt_path, row)
                replace_detail_rows_for_file(conn, detail_df, row["file_hash"])

                update_audit_success(conn, row)

                touched_file_hashes.append(row["file_hash"])
                loaded_count += 1

                write_run_log(
                    RUN_LOG_PATH,
                    f"Loaded {txt_path.name} | station={row['station']} | "
                    f"device={row['device_code']} | lot_id={row['lot_id']} | "
                    f"detail_rows={len(detail_df):,}"
                )

            except Exception as e:
                write_run_log(RUN_LOG_PATH, f"FAILED {txt_path.name} | Error: {e}")
                if conn is not None:
                    update_audit_failed(conn, txt_path, str(e))

        write_run_log(RUN_LOG_PATH, f"Skipped invalid station : {skipped_invalid_station_count}")
        write_run_log(RUN_LOG_PATH, f"Skipped unchanged files : {skipped_unchanged_count}")

        before_dedupe_cnt = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
        write_run_log(RUN_LOG_PATH, f"Header rows BEFORE dedupe: {before_dedupe_cnt:,}")

        write_run_log(RUN_LOG_PATH, "Starting duplicate lot dedupe")
        duplicates_df = dedupe_latest_lot_versions(conn)

        after_dedupe_cnt = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
        write_run_log(RUN_LOG_PATH, f"Header rows AFTER dedupe : {after_dedupe_cnt:,}")
        write_run_log(RUN_LOG_PATH, f"Rows removed by dedupe   : {before_dedupe_cnt - after_dedupe_cnt:,}")

        loaded_header_cnt = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
        distinct_lots_cnt = conn.execute(f"SELECT COUNT(DISTINCT lot_id) FROM {TABLE_NAME}").fetchone()[0]

        write_run_log(RUN_LOG_PATH, f"Current header rows      : {loaded_header_cnt:,}")
        write_run_log(RUN_LOG_PATH, f"Distinct lot_id count    : {distinct_lots_cnt:,}")

        write_run_log(RUN_LOG_PATH, "Updating KPI metrics for touched files")
        update_header_metrics_for_files(conn, touched_file_hashes)
        write_run_log(RUN_LOG_PATH, "KPI metrics update completed")

        daily_start_dt = datetime.combine(YDAY, datetime.min.time())
        daily_end_dt = daily_start_dt + timedelta(days=1)

        write_run_log(RUN_LOG_PATH, "Building customer FT yield report dataframe (previous day only)")
        daily_df = build_daily_24h_export_df(conn, daily_start_dt, daily_end_dt)
        write_run_log(RUN_LOG_PATH, f"Customer FT yield rows : {len(daily_df):,}")
        write_run_log(RUN_LOG_PATH, f"Previous day start     : {daily_start_dt}")
        write_run_log(RUN_LOG_PATH, f"Previous day end       : {daily_end_dt}")

        # ============================================
        # SECURITY VALIDATION FOR CUSTOMER CSV EXPORT
        # ============================================
        if not daily_df.empty:
            bad_customer_df = daily_df[
                daily_df["customer"].astype(str).str.strip().str.upper() != "XU"
            ]

            bad_station_df = daily_df[
                daily_df["station"].astype(str).str.strip() == "1004"
            ]

            bad_MDX_df = daily_df[
                daily_df["device_code"].astype(str).str.strip().str.upper().str.startswith("MDX")
            ]

            if not bad_customer_df.empty:
                bad_customers = sorted(
                    bad_customer_df["customer"].astype(str).str.strip().unique().tolist()
                )
                write_run_log(
                    RUN_LOG_PATH,
                    f"SECURITY CHECK FAILED: Non-scoped customer(s) found in export: {bad_customers}"
                )
                raise RuntimeError(
                    f"SECURITY CHECK FAILED: Non-scoped customer(s) found in export: {bad_customers}"
                )

            if not bad_station_df.empty:
                bad_schedules = sorted(
                    bad_station_df["schedule_no"].astype(str).str.strip().unique().tolist()
                )
                write_run_log(
                    RUN_LOG_PATH,
                    f"SECURITY CHECK FAILED: Station 1004 found in export. Schedule(s): {bad_schedules}"
                )
                raise RuntimeError(
                    f"SECURITY CHECK FAILED: Station 1004 found in export. Schedule(s): {bad_schedules}"
                )

            if not bad_MDX_df.empty:
                bad_MDX_devices = sorted(
                    bad_MDX_df["device_code"].astype(str).str.strip().unique().tolist()
                )
                write_run_log(
                    RUN_LOG_PATH,
                    f"SECURITY CHECK FAILED: MDX device(s) found in export: {bad_MDX_devices}"
                )
                raise RuntimeError(
                    f"SECURITY CHECK FAILED: MDX device(s) found in export: {bad_MDX_devices}"
                )

            export_customers = sorted(
                daily_df["customer"].astype(str).str.strip().str.upper().unique().tolist()
            )
            export_stations = sorted(
                daily_df["station"].astype(str).str.strip().unique().tolist()
            )

            write_run_log(RUN_LOG_PATH, f"Validated export customers: {export_customers}")
            write_run_log(RUN_LOG_PATH, f"Validated export stations : {export_stations}")
        else:
            write_run_log(RUN_LOG_PATH, "Daily export dataframe is empty after XU/1004/MDX filtering")

        cleanup_old_files(OVERALL_DIR, [
            "SiP_FT_Yield_Report_*_(Security C).csv",
        ])

        write_run_log(RUN_LOG_PATH, "Exporting customer FT yield report (previous day only)")
        write_csv_safely(daily_df, OVERALL_HEADER_CSV)
        write_run_log(RUN_LOG_PATH, "Customer FT yield report export completed")

    except Exception as e:
        write_run_log(RUN_LOG_PATH, f"MAIN PROCESS FAILED: {e}")
        raise

    finally:
        if conn is not None:
            write_run_log(RUN_LOG_PATH, "Closing DuckDB connection")
            conn.close()
            write_run_log(RUN_LOG_PATH, "DuckDB connection closed")

    write_run_log(RUN_LOG_PATH, f"CUSTOMER_FT_YIELD_CSV : {OVERALL_HEADER_CSV}")
    write_run_log(RUN_LOG_PATH, f"1-MONTH CUTOFF       : {cutoff_dt:%Y-%m-%d %H:%M:%S}")

    write_run_log(RUN_LOG_PATH, "Copying run log to shared folder")
    copy_file_overwrite(RUN_LOG_PATH, SHARED_RUN_LOG_PATH)
    write_run_log(RUN_LOG_PATH, f"Run log copy completed: {SHARED_RUN_LOG_PATH}")

    write_run_log(RUN_LOG_PATH, "Run Summary")
    write_run_log(RUN_LOG_PATH, f"Loaded files            : {loaded_count}")
    write_run_log(RUN_LOG_PATH, f"Skipped files           : {skipped_count}")
    write_run_log(RUN_LOG_PATH, f"Skipped invalid station : {skipped_invalid_station_count}")
    write_run_log(RUN_LOG_PATH, f"Skipped unchanged files : {skipped_unchanged_count}")
    write_run_log(RUN_LOG_PATH, f"Skipped non-scoped files    : {skipped_non_XU_count}")
    write_run_log(RUN_LOG_PATH, f"Deleted unwanted txt    : {deleted_unwanted_count}")
    write_run_log(RUN_LOG_PATH, f"Duplicate lots removed  : {len(duplicates_df)}")
    write_run_log(RUN_LOG_PATH, f"Recent txt scanned      : {len(txt_files)}")
    write_run_log(RUN_LOG_PATH, f"Touched files for KPI   : {len(set(touched_file_hashes))}")
    write_run_log(RUN_LOG_PATH, f"Customer FT Yield CSV   : {OVERALL_HEADER_CSV}")
    write_run_log(RUN_LOG_PATH, "Copying DuckDB file to shared folder")
    copy_file_overwrite(LOCAL_DB_PATH, OVERALL_DUCKDB_COPY)
    write_run_log(RUN_LOG_PATH, f"DuckDB file copy completed: {OVERALL_DUCKDB_COPY}")
    write_run_log(RUN_LOG_PATH, f"Shared Run Log          : {SHARED_RUN_LOG_PATH}")
    write_run_log(RUN_LOG_PATH, "SiP loader completed successfully")

    # Final sync again so shared log contains the full final summary
    copy_file_overwrite(RUN_LOG_PATH, SHARED_RUN_LOG_PATH)


if __name__ == "__main__":
    main()
