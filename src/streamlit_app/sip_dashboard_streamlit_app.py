#!/usr/bin/env python
# coding: utf-8

# In[2]:


import os
import shutil
import getpass
import configparser
from pathlib import Path

import duckdb
import pandas as pd
import altair as alt
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components

import math
import numpy as np
import re

import plotly.io as pio

pio.templates["custom_black"] = pio.templates["plotly_white"]

pio.templates["custom_black"].layout.update(
    font=dict(color="black"),
    xaxis=dict(
        tickfont=dict(color="black"),
        title_font=dict(color="black")
    ),
    yaxis=dict(
        tickfont=dict(color="black"),
        title_font=dict(color="black")
    )
)

pio.templates.default = "custom_black"

# =========================================================
# CONFIG
# =========================================================
SCRIPT_DIR = Path(__file__).parent

CONFIG_PATH = SCRIPT_DIR / "sip_loader_config.ini"

if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")


def load_config(config_path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(config_path, encoding="utf-8")
    return cfg


def expand_user_tokens(path_str: str, user_id: str) -> Path:
    return Path(path_str.replace("{USER_ID}", user_id))


CFG = load_config(CONFIG_PATH)

USER_ID = os.environ.get("USERNAME") or getpass.getuser()

BASE_DIR = expand_user_tokens(CFG["PATHS"]["local_base_dir"], USER_ID)
DB_PATH = BASE_DIR / CFG["OUTPUT"]["local_db_name"]

# SHARED_ROOT = Path(CFG["PATHS"]["shared_root"])
SHARED_ROOT = expand_user_tokens(CFG["PATHS"]["shared_root"], USER_ID)
SHARED_DB_DIR = SHARED_ROOT / CFG["OUTPUT"]["shared_db_subdir"]
SHARED_DAILY_HTML_DIR = SHARED_ROOT / CFG["OUTPUT"]["shared_daily_html_subdir"]
SHARED_TOP_LEVEL_HTML_DIR = SHARED_ROOT / CFG["OUTPUT"]["shared_top_level_html_subdir"]

EXPORT_DIR = BASE_DIR / "exports"

HEADER_TABLE = "file_header"
DETAIL_TABLE = "detail_2d_list"

SPECIAL_DEVICES = {"QX1", "QX2"}

# =========================================================
# UPDATED / NEW CONSTANTS
# =========================================================
YoY_PERIOD_MIN_DATE = "2023-01-01"

VALID_DASHBOARD_STATIONS = ("1000", "1001", "1002", "1004")
EXCLUDED_RETEST_STATIONS = ("1010", "1011", "1012")

STATION_MAP = {
    "1000": "PARAM",
    "1001": "FUNC1",
    "1002": "FUNC2",
    "1004": "1004",
}

QX_DEVICES = set()

MEDIUM_BLUE = "#4F81BD"
MEDIUM_LIGHT_GREEN = "#92D050"
LIGHT_RED = "#F4A6A6"

AUTO_EXPORT_MODE = True

st.set_page_config(
    page_title="SiP Yield Dashboard",
    layout="wide"
)

alt.themes.enable("default")
alt.data_transformers.disable_max_rows()

# =========================================================
# HELPERS
# =========================================================
@st.cache_data(show_spinner=False)
def run_query(query: str) -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(query).fetchdf()
    finally:
        conn.close()


def sql_safe(value: str) -> str:
    return value.replace("'", "''")

def make_scope_filter_sql_excluding_101x(
    device_code: str,
    station_value: str | None = None,
    header_alias: str = ""
) -> str:
    """
    Scope filter that excludes 1010 / 1011 / 1012 entirely.
    This is needed for OA / period / quantity charts so header-based totals
    match the VBA outputs.
    """
    h_prefix = f"{header_alias}." if header_alias else ""

    device_escaped = device_code.replace("'", "''")
    clauses = [f"COALESCE(TRIM(CAST({h_prefix}device_code AS VARCHAR)), '') = '{device_escaped}'"]

    if station_value is not None:
        station_escaped = station_value.replace("'", "''")
        clauses.append(f"COALESCE(TRIM(CAST({h_prefix}station AS VARCHAR)), '') = '{station_escaped}'")
    else:
        clauses.append(f"COALESCE(TRIM(CAST({h_prefix}station AS VARCHAR)), '') NOT IN ('1010','1011','1012')")

    return "WHERE " + " AND ".join(clauses)

def make_scope_filter_sql(
    device_code: str,
    station_value: str | None = None,
    header_alias: str = "",
    detail_alias: str = ""
) -> str:
    h_prefix = f"{header_alias}." if header_alias else ""
    _ = detail_alias

    device_escaped = device_code.replace("'", "''")
    clauses = [f"COALESCE(TRIM(CAST({h_prefix}device_code AS VARCHAR)), '') = '{device_escaped}'"]

    if station_value is not None:
        station_escaped = station_value.replace("'", "''")
        clauses.append(f"COALESCE(TRIM(CAST({h_prefix}station AS VARCHAR)), '') = '{station_escaped}'")

    return "WHERE " + " AND ".join(clauses)


def fig_to_html_fragment(fig) -> str:
    if fig is None:
        return "<p>No chart available.</p>"
    return pio.to_html(fig, include_plotlyjs=False, full_html=False)


def dataframe_to_html_table(df: pd.DataFrame, title: str = "") -> str:
    if df is None or df.empty:
        return f"<h3>{title}</h3><p>No data available.</p>" if title else "<p>No data available.</p>"

    out = df.copy()

    for col in out.columns:
        if pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].apply(
                lambda x: int(float(x)) if pd.notna(x) and float(x).is_integer() else x
            )
        else:
            out[col] = out[col].astype(str).str.replace("\n", "<br>", regex=False)

    html = out.to_html(index=False, border=1, escape=False)

    if title:
        return f"<h3>{title}</h3>{html}"
    return html


@st.cache_data(show_spinner=False)
def get_lot_list_by_scope(device_code: str, station_value: str | None) -> list[str]:
    # scope_filter = make_scope_filter_sql(device_code, station_value)
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)
    exclude_retest_filter = "AND d.station NOT IN ('1010','1011','1012')"
    query = f"""
        SELECT DISTINCT schedule_no
        FROM {HEADER_TABLE}
        {scope_filter}
          AND schedule_no IS NOT NULL
          AND TRIM(CAST(schedule_no AS VARCHAR)) <> ''
        ORDER BY schedule_no
    """
    df = run_query(query)
    return ["ALL"] + df["schedule_no"].astype(str).tolist()


def add_date_range_slider(
    df: pd.DataFrame,
    key_prefix: str,
    label: str = "Select date range",
    default_days_ending_yesterday: int | None = None,
    default_last_n_days_from_data_max: int | None = None
) -> pd.DataFrame:
    if df.empty or "test_date" not in df.columns:
        return df

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")

    if out.empty:
        return out

    min_date = out["test_date"].dt.date.min()
    max_date = out["test_date"].dt.date.max()

    if min_date == max_date:
        st.caption(f"{label}: only one available date ({min_date})")
        return out.copy()

    default_value = (min_date, max_date)

    # Default to last N days ending at latest available data date
    if default_last_n_days_from_data_max is not None and default_last_n_days_from_data_max > 0:
        default_end = max_date
        default_start = max_date - pd.Timedelta(days=default_last_n_days_from_data_max - 1)

        clamped_start = max(min_date, default_start)
        clamped_end = max_date

        if clamped_start <= clamped_end:
            default_value = (clamped_start, clamped_end)

    # Default to day-1 or last N days ending at day-1
    elif default_days_ending_yesterday is not None and default_days_ending_yesterday > 0:
        yesterday = pd.Timestamp.today().date() - pd.Timedelta(days=1)

        # Find latest available data date that is <= yesterday
        available_dates = sorted(out["test_date"].dt.date.unique().tolist())
        eligible_dates = [d for d in available_dates if d <= yesterday]

        if eligible_dates:
            default_end = max(eligible_dates)
        else:
            # fallback: if no data on/before yesterday, use latest available date
            default_end = max_date

        default_start = default_end - pd.Timedelta(days=default_days_ending_yesterday - 1)

        clamped_start = max(min_date, default_start)
        clamped_end = default_end

        if clamped_start <= clamped_end:
            default_value = (clamped_start, clamped_end)

    selected_range = st.slider(
        label,
        min_value=min_date,
        max_value=max_date,
        value=default_value,
        key=f"{key_prefix}_date_slider"
    )

    start_date, end_date = selected_range

    return out[
        (out["test_date"].dt.date >= start_date) &
        (out["test_date"].dt.date <= end_date)
    ].copy()

def coerce_integer_columns(df: pd.DataFrame, int_cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    out = df.copy()
    for col in int_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
            out[col] = out[col].apply(lambda x: int(x) if pd.notna(x) else x)
    return out


def combine_pct_and_label_for_display(
    df: pd.DataFrame,
    label_pct_pairs: list[tuple[str, str]],
    other_pct_col: str | None = None,
    other_label: str = "Other errCodes"
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    def format_two_line_label(label_val, pct_val):
        if pd.isna(pct_val):
            return "-"

        label_str = str(label_val).strip()
        if label_str in {"", "-"}:
            return "-"

        parts = label_str.split("\n", 1)

        if len(parts) == 2:
            soft_bin = parts[0].strip()
            err_desc = parts[1].strip()
        else:
            soft_bin = label_str.strip()
            err_desc = ""

        if err_desc:
            return f"{float(pct_val):.2f}% {soft_bin}\n{err_desc}"
        return f"{float(pct_val):.2f}% {soft_bin}"

    for label_col, pct_col in label_pct_pairs:
        if label_col in out.columns and pct_col in out.columns:
            out[label_col] = out.apply(
                lambda r: format_two_line_label(r[label_col], r[pct_col]),
                axis=1
            )
            out = out.drop(columns=[pct_col])

    if other_pct_col and other_pct_col in out.columns:
        out[other_pct_col] = out[other_pct_col].apply(
            lambda x: f"{float(x):.2f}%" if pd.notna(x) else "-"
        )

    return out

def transpose_metric_table(df: pd.DataFrame, date_col: str = "Date") -> pd.DataFrame:
    if df is None or df.empty or date_col not in df.columns:
        return pd.DataFrame()
    return df.set_index(date_col).T.reset_index().rename(columns={"index": "Metric"})

def build_qx_unknown_exclSIPon_sql(
    device_code_value: str,
    station_value: str | None,
    sb_expr: str,
    err_expr: str
) -> str:
    """
    Exclude UNKNOWN soft_bin / errCode only for:
    - QX1 / QX2
    - station 1001 / 1002 (FUNC1 / FUNC2)
    Keep UNKNOWN for:
    - PARAM (1000)
    - OA / Overall (station None)
    """
    if device_code_value in {"QX1", "QX2"} and station_value in {"1001", "1002"}:
        return f"AND NOT (({sb_expr}) = 'UNKNOWN' OR ({err_expr}) = 'UNKNOWN')"
    return ""

def format_pct_value(x, decimals: int = 2) -> str:
    if pd.isna(x):
        return "-"
    s = str(x).strip()
    if s in {"", "-", "nan", "None"}:
        return "-"
    try:
        if s.endswith("%"):
            s = s[:-1].strip()
        return f"{float(s):.{decimals}f}%"
    except Exception:
        return str(x)


def format_pct_rows_in_transposed_table(
    df: pd.DataFrame,
    metric_names: list[str],
    decimals: int = 2
) -> pd.DataFrame:
    if df is None or df.empty or "Metric" not in df.columns:
        return df

    out = df.copy()
    metric_mask = out["Metric"].isin(metric_names)

    for col in out.columns:
        if col != "Metric":
            out.loc[metric_mask, col] = out.loc[metric_mask, col].apply(
                lambda x: format_pct_value(x, decimals=decimals)
            )

    return out

def format_pct_columns(
    df: pd.DataFrame,
    col_names: list[str],
    decimals: int = 2
) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    out = df.copy()
    for col in col_names:
        if col in out.columns:
            out[col] = out[col].apply(lambda x: format_pct_value(x, decimals=decimals))

    return out

def order_metric_rows(df: pd.DataFrame, metric_order: list[str]) -> pd.DataFrame:
    if df is None or df.empty or "Metric" not in df.columns:
        return df

    out = df.copy()
    out["Metric"] = pd.Categorical(out["Metric"], categories=metric_order, ordered=True)
    out = out.sort_values("Metric").reset_index(drop=True)
    out["Metric"] = out["Metric"].astype(str)
    return out

def get_device_scope_filter(device_code: str) -> str:
    dc = (device_code or "").strip().upper()

    if dc in ("QX1", "QX2"):
        return "device_code IN ('QX1','QX2')"

    elif dc in ("QX1-NPI", "QX2-NPI"):
        return "device_code IN ('QX1-NPI','QX2-NPI')"

    else:
        return f"device_code = '{dc}'"

def ensure_folder(folder_path: Path) -> None:
    folder_path.mkdir(parents=True, exist_ok=True)


def cleanup_folder_contents(folder: Path, patterns: list[str] | None = None) -> None:
    ensure_folder(folder)

    if patterns is None:
        patterns = ["*"]

    for pattern in patterns:
        for item in folder.glob(pattern):
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                st.warning(f"Unable to delete {item}: {e}")

def filter_previous_day_only(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if df is None or df.empty:
        return pd.DataFrame(), "No data"

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"])

    if out.empty:
        return pd.DataFrame(), "No data"

    # test_date is already CAST(h.end_time AS DATE) from SQL
    latest_day = out["test_date"].dt.date.max()

    prev_day_df = out[out["test_date"].dt.date == latest_day].copy()

    date_scope_label = (
        f"{latest_day.strftime('%Y-%m-%d')} 00:00:00 "
        f"to {latest_day.strftime('%Y-%m-%d')} 23:59:59"
    )

    return prev_day_df, date_scope_label

def get_detail_join_condition(header_alias="h", detail_alias="d", station_value=None):
    if station_value == "1004":
        return f"""
            TRIM(CAST({header_alias}.schedule_no AS VARCHAR)) =
            TRIM(CAST({detail_alias}.schedule_no AS VARCHAR))
        """
    return f"{detail_alias}.file_hash = {header_alias}.file_hash"

def get_top5_defect_labels_from_reference_period(
    err_df: pd.DataFrame,
    reference_mask
) -> list[str]:
    ref = err_df[reference_mask].copy()

    ref = ref[
        (pd.to_numeric(ref["fail_qty"], errors="coerce").fillna(0) > 0) &
        (ref["soft_bin"].astype(str) != "-")
    ].copy()

    if ref.empty:
        return []

    ref["err_label"] = (
        ref["soft_bin"].astype(str).str.strip() + "\n" +
        ref["errCode"].astype(str).str.strip()
    )

    top = (
        ref.groupby("err_label", as_index=False)
        .agg(fail_qty=("fail_qty", "sum"))
        .sort_values(["fail_qty", "err_label"], ascending=[False, True])
        .head(5)
    )

    return top["err_label"].tolist()

def get_top5_labels_latest_day(err_df: pd.DataFrame) -> list[str]:
    if err_df.empty:
        return []

    df = err_df.copy()
    df["test_date"] = pd.to_datetime(df["test_date"], errors="coerce")

    latest_day = df["test_date"].dt.date.max()

    df = df[df["test_date"].dt.date == latest_day].copy()

    df["fail_qty"] = pd.to_numeric(df["fail_qty"], errors="coerce").fillna(0)

    df = df[df["fail_qty"] > 0]

    df["err_label"] = (
        df["soft_bin"].astype(str).str.strip() + "\n" +
        df["errCode"].astype(str).str.strip()
    )

    top5 = (
        df.groupby("err_label", as_index=False)
        .agg(fail_qty=("fail_qty", "sum"))
        .sort_values(["fail_qty", "err_label"], ascending=[False, True])
        .head(5)
    )

    return top5["err_label"].tolist()


# =========================================================
# KPI CARD HELPERS
# =========================================================
def get_kpi_status_color(metric_name: str, value, fty_target: float | None = None) -> tuple[str, str]:
    if pd.isna(value):
        return "#E6E6E6", "N/A"

    v = float(value)

    if metric_name == "FTY":
        target = fty_target if fty_target is not None else 98.0

        if v < target:
            return "#F4A6A6", "RED"
        elif v <= (target + 0.25):
            return "#FFF2CC", "YELLOW"
        return "#C6E0B4", "GREEN"

    if metric_name == "FPY":
        if v < 95:
            return "#F4A6A6", "RED"
        elif v < 96:
            return "#FFF2CC", "YELLOW"
        return "#C6E0B4", "GREEN"

    if metric_name == "RPR":
        if v > 3:
            return "#F4A6A6", "RED"
        elif v > 2:
            return "#FFF2CC", "YELLOW"
        return "#C6E0B4", "GREEN"

    if metric_name == "LRR":
        if v > 0:
            return "#F4A6A6", "RED"
        return "#C6E0B4", "GREEN"

    return "#F2F2F2", ""

def assign_fty_target_to_kpi_bucket(bucket_label: str, sort_key, target_info: dict) -> float | None:
    if not target_info:
        return 98.00
    return float(target_info.get("current_month_fty_target_lcl", 98.00))

# =========================================================
# KPI TARGET SOURCE - FULL HISTORY
# =========================================================
@st.cache_data(show_spinner=False)
def get_kpi_target_source_df(device_code: str, station_value: str | None) -> pd.DataFrame:
    """
    Full scoped daily trend source for KPI target calculation.
    Uses whole scoped history, not just 4 weeks.
    """
    query = get_station_period_trend_sql(device_code, station_value)
    return run_query(query)


# =========================================================
# KPI TARGET HELPERS
# =========================================================

@st.cache_data(show_spinner=False)
def get_monthly_lot_target_source_df(device_code: str, station_value: str | None) -> pd.DataFrame:
    """
    Returns lot-level monthly source for FPY / FTY target calculation.
    Uses the same device/station scope as the per-device tab.
    """
    # scope_filter = make_scope_filter_sql(device_code, station_value)
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)
    exclude_retest_filter = "AND d.station NOT IN ('1010','1011','1012')"

    return run_query(f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {scope_filter}
              AND CAST(end_time AS DATE) >= DATE '{YoY_PERIOD_MIN_DATE}'
        ),
        latest_header_per_lot_day AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    CAST(end_time AS DATE) AS test_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_code, station, schedule_no, CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
                WHERE schedule_no IS NOT NULL
                  AND TRIM(CAST(schedule_no AS VARCHAR)) <> ''
            ) x
            WHERE rn = 1
        )
        SELECT
            test_date,
            schedule_no,
            SUM(COALESCE(input_quantity, 0)) AS input_quantity,
            SUM(COALESCE(first_pass_qty, 0)) AS first_pass_qty,
            SUM(COALESCE(final_pass_qty, 0)) AS final_pass_qty
        FROM latest_header_per_lot_day
        GROUP BY test_date, schedule_no
        ORDER BY test_date, schedule_no
    """)

def calc_iqr_filtered_3sig_lcl(values: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """
    IQR robust LCL:
    1. Calculate Q1/Q3/IQR
    2. Remove lower/upper outliers outside Q1 - 1.5*IQR and Q3 + 1.5*IQR
    3. Calculate avg - 3*sigma from remaining values
    """
    vals = pd.to_numeric(values, errors="coerce").dropna().astype(float)

    if vals.empty:
        return None, None, None, 0

    q1 = vals.quantile(0.25)
    q3 = vals.quantile(0.75)
    iqr = q3 - q1

    lower_limit = q1 - (1.5 * iqr)
    upper_limit = q3 + (1.5 * iqr)

    kept = vals[(vals >= lower_limit) & (vals <= upper_limit)].copy()

    # fallback: if IQR filter removes everything, use original values
    if kept.empty:
        kept = vals.copy()

    avg_val = float(kept.mean())
    sigma_val = float(kept.std(ddof=0))
    raw_lcl = round(avg_val - (3 * sigma_val), 2)

    return round(avg_val, 2), round(sigma_val, 2), raw_lcl, int(len(kept))

def find_latest_back_month_with_data(work: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.DataFrame]:
    """
    Finds the latest prior month before current month that has data.
    If previous month has no data, go back month by month until data is found.
    """
    if work is None or work.empty:
        return None, pd.DataFrame()

    out = work.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).copy()

    if out.empty:
        return None, pd.DataFrame()

    latest_ts = out["test_date"].max()
    current_month_start = pd.Timestamp(year=latest_ts.year, month=latest_ts.month, day=1)

    # candidate months before current month, descending
    out["month_start"] = out["test_date"].apply(lambda x: pd.Timestamp(year=x.year, month=x.month, day=1))
    candidate_months = sorted(
        [m for m in out["month_start"].dropna().unique().tolist() if pd.Timestamp(m) < current_month_start],
        reverse=True
    )

    for month_start in candidate_months:
        sub = out[out["month_start"] == pd.Timestamp(month_start)].copy()
        if not sub.empty:
            return pd.Timestamp(month_start), sub

    return None, pd.DataFrame()

def get_scope_target_limits_from_back_months(monthly_lot_df: pd.DataFrame) -> dict:
    """
    Target logic:
    - Use latest previous/back month with available data
    - Calculate lot-level FPY / FTY
    - Remove outliers SIPng IQR rule
    - Calculate 3-sigma LCL from remaining lots
    - Clamp negative LCL to 0
    - FTY target cannot be lower than FPY target
    """
    default_result = {
        "current_month_fpy_target_lcl": 95.00,
        "current_month_fty_target_lcl": 98.00,
        "current_month_fpy_raw_lcl": None,
        "current_month_fty_raw_lcl": None,
        "target_source_month_start": None,
        "target_source_month_label": None,
        "no_back_month_data": True,
        "new_device_static_label": "Newly added device: No back month data available to calculate IQR-based 3-sig LCL FPY/FTY limits"
    }

    if monthly_lot_df is None or monthly_lot_df.empty:
        return default_result

    work = monthly_lot_df.copy()
    work["test_date"] = pd.to_datetime(work["test_date"], errors="coerce")
    work = work.dropna(subset=["test_date"]).copy()

    if work.empty:
        return default_result

    work["input_quantity"] = pd.to_numeric(work["input_quantity"], errors="coerce")
    work["first_pass_qty"] = pd.to_numeric(work["first_pass_qty"], errors="coerce")
    work["final_pass_qty"] = pd.to_numeric(work["final_pass_qty"], errors="coerce")

    work = work[work["input_quantity"].fillna(0) > 0].copy()

    if work.empty:
        return default_result

    work["fpy_pct"] = (100.0 * work["first_pass_qty"] / work["input_quantity"]).round(2)
    work["fty_pct"] = (100.0 * work["final_pass_qty"] / work["input_quantity"]).round(2)

    month_start, month_df = find_latest_back_month_with_data(work)

    if month_df.empty or month_start is None:
        return default_result

    fpy_avg, fpy_sigma, fpy_raw_lcl, fpy_kept_n = calc_iqr_filtered_3sig_lcl(month_df["fpy_pct"])
    fty_avg, fty_sigma, fty_raw_lcl, fty_kept_n = calc_iqr_filtered_3sig_lcl(month_df["fty_pct"])

    if fpy_raw_lcl is None:
        fpy_lcl = 95.00
    else:
        fpy_lcl = max(0.00, round(fpy_raw_lcl, 2))

    if fty_raw_lcl is None:
        fty_lcl = 98.00
    else:
        fty_lcl = max(0.00, round(fty_raw_lcl, 2))

    # Keep FTY target logically not lower than FPY target
    fty_lcl = max(fty_lcl, fpy_lcl)

    return {
        "current_month_fpy_target_lcl": fpy_lcl,
        "current_month_fty_target_lcl": fty_lcl,
        "current_month_fpy_raw_lcl": fpy_lcl,
        "current_month_fty_raw_lcl": fty_lcl,
        "target_source_month_start": month_start,
        "target_source_month_label": format_month_label(month_start),
        "no_back_month_data": False,
        "new_device_static_label": ""
    }

def format_month_label(ts: pd.Timestamp) -> str:
    return ts.strftime("%b'%y")


def calc_month_fty_target_from_daily_df(month_df: pd.DataFrame) -> tuple[float | None, float]:
    """
    Returns:
    - raw_lcl: avg daily FTY - 3 sigma
    - effective_target: max(raw_lcl, 95.00), or 95.00 if no dataset
    """
    if month_df is None or month_df.empty:
        return None, 95.00

    work = month_df.copy()

    if "final_yield_pct" in work.columns:
        daily_fty = pd.to_numeric(work["final_yield_pct"], errors="coerce").dropna()
    else:
        input_qty = pd.to_numeric(work["input_quantity"], errors="coerce")
        final_pass_qty = pd.to_numeric(work["final_pass_qty"], errors="coerce")
        daily_fty = (100.0 * final_pass_qty / input_qty.replace(0, pd.NA)).dropna()

    if daily_fty.empty:
        return None, 95.00

    avg_val = daily_fty.mean()
    sigma_val = daily_fty.std(ddof=0)

    raw_lcl = round(avg_val - (3 * sigma_val), 2)
    effective_target = max(95.00, raw_lcl)

    return raw_lcl, effective_target

def get_fty_target_for_date(ts, target_info: dict) -> float:
    if ts is None or not target_info:
        return 95.00

    ts = pd.Timestamp(ts)
    current_month_start = target_info.get("current_month_start")
    previous_month_start = target_info.get("previous_month_start")

    if current_month_start is not None and ts >= current_month_start:
        return float(target_info.get("current_month_target", 95.00))

    if previous_month_start is not None and current_month_start is not None:
        if previous_month_start <= ts < current_month_start:
            return float(target_info.get("previous_month_target", 95.00))

    return float(target_info.get("previous_month_target", 95.00))

def get_month_fty_targets_from_history(full_daily_df: pd.DataFrame) -> dict:
    """
    Returns target info for KPI labels + KPI card coloring.
    """
    if full_daily_df is None or full_daily_df.empty:
        return {
            "current_month_label": None,
            "previous_month_label": None,
            "current_month_raw_lcl": None,
            "previous_month_raw_lcl": None,
            "current_month_target": 95.00,
            "previous_month_target": 95.00,
            "current_month_start": None,
            "previous_month_start": None,
        }

    work = full_daily_df.copy()
    work["test_date"] = pd.to_datetime(work["test_date"], errors="coerce")
    work = work.dropna(subset=["test_date"]).sort_values("test_date")

    if work.empty:
        return {
            "current_month_label": None,
            "previous_month_label": None,
            "current_month_raw_lcl": None,
            "previous_month_raw_lcl": None,
            "current_month_target": 95.00,
            "previous_month_target": 95.00,
            "current_month_start": None,
            "previous_month_start": None,
        }

    latest_ts = work["test_date"].max()
    current_month_start = pd.Timestamp(year=latest_ts.year, month=latest_ts.month, day=1)
    previous_month_start = current_month_start - pd.DateOffset(months=1)
    prior_month_start = current_month_start - pd.DateOffset(months=2)

    current_month_label = format_month_label(current_month_start)
    previous_month_label = format_month_label(previous_month_start)

    previous_month_end = current_month_start - pd.Timedelta(days=1)
    prior_month_end = previous_month_start - pd.Timedelta(days=1)

    # Current month target = based on previous full month
    prev_month_df = work[
        (work["test_date"] >= previous_month_start) &
        (work["test_date"] <= previous_month_end)
    ].copy()

    # Previous month target = based on month before previous
    prior_month_df = work[
        (work["test_date"] >= prior_month_start) &
        (work["test_date"] <= prior_month_end)
    ].copy()

    current_month_raw_lcl, current_month_target = calc_month_fty_target_from_daily_df(prev_month_df)
    previous_month_raw_lcl, previous_month_target = calc_month_fty_target_from_daily_df(prior_month_df)

    return {
        "current_month_label": current_month_label,
        "previous_month_label": previous_month_label,
        "current_month_raw_lcl": current_month_raw_lcl,
        "previous_month_raw_lcl": previous_month_raw_lcl,
        "current_month_target": current_month_target,
        "previous_month_target": previous_month_target,
        "current_month_start": current_month_start,
        "previous_month_start": previous_month_start,
    }

def get_kpi_target_labels_html(full_daily_df: pd.DataFrame, monthly_lot_df: pd.DataFrame) -> str:
    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)

    if target_info["no_back_month_data"]:
        return f"""
        <div style="font-size:13px; color:#666; margin-bottom:10px; line-height:1.6;">
            FTY Target (3-sig LCL): 98.00%<br>
            FPY Target (3-sig LCL): 95.00%<br>
            LRR Hold Trigger: FTY Target (3-sig LCL): 98.00%<br>
            RPR Threshold: &lt; 3%<br>
            <span style="color:#C00000; font-weight:700;">
                {target_info["new_device_static_label"]}
            </span>
        </div>
        """

    source_month = target_info.get("target_source_month_label") or "-"
    fty_raw = target_info.get("current_month_fty_raw_lcl")
    fpy_raw = target_info.get("current_month_fpy_raw_lcl")

    fty_text = f"{fty_raw:.2f}%" if fty_raw is not None else "98.00%"
    fpy_text = f"{fpy_raw:.2f}%" if fpy_raw is not None else "95.00%"

    return f"""
    <div style="font-size:13px; color:#666; margin-bottom:10px; line-height:1.6;">
        Back Month Used for IQR Target Calc: {{{source_month}}}<br>
        FTY Target (3-sig LCL): {fty_text}<br>
        FPY Target (3-sig LCL): {fpy_text}<br>
        LRR Hold Trigger: FTY Target (3-sig LCL): {fty_text}<br>
        RPR Threshold: &lt; 3%
    </div>
    """

def get_top10_rpr_errcode_pareto_sql(
    device_code_value: str,
    station_value: str | None = None,
    selected_schedule: str = "ALL"
) -> str:
    filters = [
        f"COALESCE(TRIM(CAST(h.device_code AS VARCHAR)), '') = '{sql_safe(device_code_value)}'"
    ]

    if station_value is not None:
        filters.append(
            f"COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') = '{sql_safe(station_value)}'"
        )
    else:
        filters.append(
            "COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') NOT IN ('1010','1011','1012')"
        )

    if selected_schedule != "ALL":
        filters.append(f"h.schedule_no = '{sql_safe(selected_schedule)}'")

    where_sql = "WHERE " + " AND ".join(filters)

    unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
        device_code_value=device_code_value,
        station_value=station_value,
        sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
        err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
    )

    excluded_retest_station_sql = build_excluded_retest_station_sql(
        header_station_expr="h.station",
        flow_expr="d.flow"
    )

    return f"""
        WITH joined_data AS (
            SELECT
                h.device_code,
                h.station,
                h.schedule_no,
                CAST(h.end_time AS DATE) AS test_date,
                d.serial_no,
                d.flow,
                COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            {where_sql}
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
            {unknown_exclSIPon_sql}
            {excluded_retest_station_sql}
        ),
        max_day AS (
            SELECT MAX(test_date) AS latest_day
            FROM joined_data
        ),
        base AS (
            SELECT *
            FROM joined_data
            WHERE test_date BETWEEN
                (SELECT latest_day - INTERVAL 27 DAY FROM max_day)
                AND
                (SELECT latest_day FROM max_day)
        ),
        input_base AS (
            SELECT
                test_date,
                COUNT(DISTINCT serial_no) AS total_input_qty
            FROM base
            GROUP BY test_date
        ),
        ft_first AS (
            SELECT *
            FROM base
            WHERE flow = 'FT'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime ASC
            ) = 1
        ),
        final_latest AS (
            SELECT *
            FROM base
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime DESC
            ) = 1
        ),
        ft_fail_by_err AS (
            SELECT
                f.test_date,
                f.soft_bin,
                f.errCode,
                COUNT(DISTINCT f.serial_no) AS ft_fail_qty
            FROM ft_first f
            WHERE f.pf_status = 'FAIL'
            GROUP BY f.test_date, f.soft_bin, f.errCode
        ),
        recovered_by_err AS (
            SELECT
                f.test_date,
                f.soft_bin,
                f.errCode,
                COUNT(DISTINCT f.serial_no) AS recovered_qty
            FROM ft_first f
            INNER JOIN final_latest l
                ON f.test_date = l.test_date
               AND f.schedule_no = l.schedule_no
               AND f.serial_no = l.serial_no
            WHERE f.pf_status = 'FAIL'
              AND l.pf_status = 'PASS'
            GROUP BY f.test_date, f.soft_bin, f.errCode
        )
        SELECT
            x.test_date,
            x.soft_bin,
            x.errCode,
            x.ft_fail_qty,
            COALESCE(r.recovered_qty, 0) AS recovered_qty,
            i.total_input_qty,
            ROUND(
                100.0 * COALESCE(r.recovered_qty, 0) / NULLIF(i.total_input_qty, 0),
                2
            ) AS recovery_contribution_pct,
            ROUND(
                100.0 * COALESCE(r.recovered_qty, 0) / NULLIF(x.ft_fail_qty, 0),
                2
            ) AS recovery_rate_pct
        FROM ft_fail_by_err x
        LEFT JOIN recovered_by_err r
            ON x.test_date = r.test_date
           AND x.soft_bin = r.soft_bin
           AND x.errCode = r.errCode
        INNER JOIN input_base i
            ON x.test_date = i.test_date
        ORDER BY x.test_date, recovery_contribution_pct DESC, recovered_qty DESC, errCode, soft_bin
    """

def build_4week_kpi_summary_df(
    daily_trend_df: pd.DataFrame,
    daily_summary_raw_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Returns:
    - 3 previous weeks
    - current running week
    - +7 daily buckets (same as chart)
    """

    if daily_trend_df.empty:
        return pd.DataFrame()

    trend_df = build_4week_trend_display_df(daily_trend_df)
    if trend_df.empty:
        return pd.DataFrame()

    # ✅ KEEP ALL BUCKETS (week + running + day)
    trend_all = trend_df.copy()

    lrr_df = build_4week_lrr_display_df(daily_summary_raw_df)
    if lrr_df.empty:
        trend_all["LRR"] = None
    else:
        lrr_all = lrr_df[["x_label", "lrr_pct"]].copy()
        trend_all = trend_all.merge(
            lrr_all,
            left_on="x_label",
            right_on="x_label",
            how="left"
        )

    out = trend_all.rename(columns={
        "x_label": "bucket",
        "1st Yield": "FPY",
        "Final Yield": "FTY",
        "Retest rate": "RPR",
        "Test-In QTY": "Input QTY",
        "Final Output": "Output QTY",
        "lrr_pct": "LRR",
    })

    # keep order same as charts
    out = out.sort_values("sort_key").reset_index(drop=True)

    return out[[
        "bucket",
        "sort_key",
        "FTY",
        "FPY",
        "RPR",
        "LRR",
        "Input QTY",
        "Output QTY",
    ]].copy()

def render_kpi_cards(
    device_code: str,
    station_value: str | None,
    daily_trend_df: pd.DataFrame,
    daily_summary_raw_df: pd.DataFrame,
    section_label: str
) -> tuple[str, pd.DataFrame, str]:
    st.subheader("KPI Cards - Past 4 Weeks")

    full_daily_df = get_kpi_target_source_df(device_code, station_value)
    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)

    target_labels_html = get_kpi_target_labels_html(full_daily_df, monthly_lot_df)
    st.markdown(target_labels_html, unsafe_allow_html=True)

    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)

    html, kpi_df = build_kpi_cards_html(
        daily_trend_df,
        daily_summary_raw_df,
        target_info=target_info
    )

    if kpi_df.empty:
        st.info("No KPI card data available.")
        return html, pd.DataFrame(), target_labels_html

    components.html(html, height=620, scrolling=True)
    return html, kpi_df, target_labels_html

def build_kpi_cards_html(
    daily_trend_df: pd.DataFrame,
    daily_summary_raw_df: pd.DataFrame,
    target_info: dict | None = None
) -> tuple[str, pd.DataFrame]:
    kpi_df = build_4week_kpi_summary_df(daily_trend_df, daily_summary_raw_df)

    if kpi_df.empty:
        return "<p>No KPI card data available.</p>", pd.DataFrame()

    metric_order = ["FTY", "FPY", "RPR", "LRR", "Input QTY", "Output QTY"]

    bucket_blocks = []

    for _, row in kpi_df.iterrows():
        bucket = row["bucket"]

        metric_blocks = []
        for metric in metric_order:
            value = row.get(metric)

            if metric in {"Input QTY", "Output QTY"}:
                display_value = f"{int(value):,}" if pd.notna(value) else "-"
                bg_color = "#F2F2F2"
            else:
                if metric == "FTY":
                    fty_target = assign_fty_target_to_kpi_bucket(
                        bucket_label=row.get("bucket"),
                        sort_key=row.get("sort_key"),
                        target_info=target_info or {}
                    )
                else:
                    fty_target = None

                bg_color, _ = get_kpi_status_color(metric, value, fty_target=fty_target)
                display_value = f"{float(value):.2f}%" if pd.notna(value) else "-"

            metric_blocks.append(
                f"""
                <div style="
                    background:{bg_color};
                    border:1px solid #D9D9D9;
                    border-radius:8px;
                    padding:10px 6px;
                    margin-bottom:8px;
                    min-height:72px;
                    display:flex;
                    flex-direction:column;
                    align-items:center;
                    justify-content:center;
                    box-sizing:border-box;
                ">
                    <div style="font-size:13px; font-weight:700; margin-bottom:4px;">
                        {metric}
                    </div>
                    <div style="font-size:20px; font-weight:800;">
                        {display_value}
                    </div>
                </div>
                """
            )

        bucket_blocks.append(
            f"""
            <div style="
                min-width:140px;
                max-width:160px;
                flex:1 1 140px;
            ">
                <div style="
                    background:#DCE6F1;
                    border:1px solid #B8CCE4;
                    border-radius:10px;
                    padding:10px;
                    margin-bottom:8px;
                    text-align:center;
                    font-weight:800;
                    font-size:18px;
                ">
                    {bucket}
                </div>
                {''.join(metric_blocks)}
            </div>
            """
        )

    html = f"""
    <div style="
        width:100%;
        oveFUNC2low-x:auto;
        padding:4px 0 8px 0;
    ">
        <div style="
            display:flex;
            flex-wrap:nowrap;
            gap:12px;
            align-items:flex-start;
            min-width:max-content;
            font-family:Arial, sans-serif;
            color:black;
        ">
            {''.join(bucket_blocks)}
        </div>
    </div>
    """

    return html, kpi_df

def get_period_fty_summary_html(plot_df: pd.DataFrame, period_label: str, section_label: str) -> str:
    if plot_df is None or plot_df.empty:
        return f"<div style='font-size:14px;'><b>{section_label}</b>: N/A</div>"

    latest_year = pd.Timestamp.today().year

    if period_label == "YoY":
        current_row = plot_df[plot_df["bucket_type"] == "year_running_total"].copy()
        prior_row = plot_df[plot_df["bucket_type"] == "year"].sort_values("sort_key").tail(1).copy()
        compare_label = str(latest_year - 1)
    elif period_label == "QoQ":
        current_row = plot_df[plot_df["bucket_type"] == "quarter_running_total"].copy()
        prior_row = plot_df[plot_df["bucket_type"] == "quarter"].sort_values("sort_key").tail(1).copy()
        compare_label = "prior quarter"
    else:
        current_row = plot_df[plot_df["bucket_type"] == "month_running_total"].copy()
        prior_row = plot_df[plot_df["bucket_type"] == "month"].sort_values("sort_key").tail(1).copy()
        compare_label = "prior month"

    curr_fty = current_row["FTY"].iloc[0] if not current_row.empty else None
    prior_fty = prior_row["FTY"].iloc[0] if not prior_row.empty else None

    if pd.isna(curr_fty):
        return f"<div style='font-size:14px;'><b>{section_label}</b>: N/A</div>"

    color = get_fty_status_color(curr_fty)

    if pd.isna(prior_fty):
        delta_text = "(vs prior: N/A)"
    else:
        delta = float(curr_fty) - float(prior_fty)
        sign = "+" if delta >= 0 else ""
        delta_text = f"({sign}{delta:.2f}% vs {compare_label})"

    return (
        f"<div style='font-size:16px; margin-bottom:8px;'>"
        f"<b>{section_label} Yield Trend - {period_label}: {latest_year} FTY @</b> "
        f"<span style='color:{color}; font-weight:700; font-size:16px;'>"
        f"{float(curr_fty):.2f}% {delta_text}"
        f"</span></div>"
    )

# =========================================================
# CONTROL LIMIT HELPERS
# =========================================================
def calc_month_metric_limits_from_daily_df(
    month_df: pd.DataFrame,
    value_col: str
) -> tuple[float | None, float | None, float | None]:
    """
    Returns:
    - avg
    - lcl = avg - 3 sigma
    - ucl = avg + 3 sigma
    """
    if month_df is None or month_df.empty or value_col not in month_df.columns:
        return None, None, None

    vals = pd.to_numeric(month_df[value_col], errors="coerce").dropna()
    if vals.empty:
        return None, None, None

    avg_val = float(vals.mean())
    sigma_val = float(vals.std(ddof=0))

    lcl = round(avg_val - (3 * sigma_val), 2)
    ucl = round(avg_val + (3 * sigma_val), 2)

    return round(avg_val, 2), lcl, ucl


def get_month_control_limits_from_history(full_daily_df: pd.DataFrame) -> dict:
    """
    Yield:
      current_month_target_lcl = previous full month's FTY LCL, floor at 95
      previous_month_target_lcl = month before previous FTY LCL, floor at 95

    LRR:
      current_month_lrr_ucl = previous full month's LRR UCL, floor at 0
      previous_month_lrr_ucl = month before previous LRR UCL, floor at 0
    """
    base_default = {
        "current_month_start": None,
        "previous_month_start": None,
        "current_month_target_lcl": 95.00,
        "previous_month_target_lcl": 95.00,
        "current_month_raw_lcl": None,
        "previous_month_raw_lcl": None,
        "current_month_lrr_ucl": 0.00,
        "previous_month_lrr_ucl": 0.00,
        "current_month_raw_lrr_ucl": None,
        "previous_month_raw_lrr_ucl": None,
    }

    if full_daily_df is None or full_daily_df.empty:
        return base_default

    work = full_daily_df.copy()
    work["test_date"] = pd.to_datetime(work["test_date"], errors="coerce")
    work = work.dropna(subset=["test_date"]).sort_values("test_date")
    if work.empty:
        return base_default

    latest_ts = work["test_date"].max()
    current_month_start = pd.Timestamp(year=latest_ts.year, month=latest_ts.month, day=1)
    previous_month_start = current_month_start - pd.DateOffset(months=1)
    prior_month_start = current_month_start - pd.DateOffset(months=2)

    previous_month_end = current_month_start - pd.Timedelta(days=1)
    prior_month_end = previous_month_start - pd.Timedelta(days=1)

    prev_month_df = work[
        (work["test_date"] >= previous_month_start) &
        (work["test_date"] <= previous_month_end)
    ].copy()

    prior_month_df = work[
        (work["test_date"] >= prior_month_start) &
        (work["test_date"] <= prior_month_end)
    ].copy()

    # ---- FTY LCL ----
    prev_avg, prev_lcl, _ = calc_month_metric_limits_from_daily_df(prev_month_df, "final_yield_pct")
    prior_avg, prior_lcl, _ = calc_month_metric_limits_from_daily_df(prior_month_df, "final_yield_pct")

    current_month_target_lcl = 95.00 if prev_lcl is None or prev_lcl < 95 else round(prev_lcl, 2)
    previous_month_target_lcl = 95.00 if prior_lcl is None or prior_lcl < 95 else round(prior_lcl, 2)

    # ---- LRR UCL ----
    prev_lrr_avg, _, prev_lrr_ucl = calc_month_metric_limits_from_daily_df(prev_month_df, "lrr_pct")
    prior_lrr_avg, _, prior_lrr_ucl = calc_month_metric_limits_from_daily_df(prior_month_df, "lrr_pct")

    current_month_lrr_ucl = 0.00 if prev_lrr_ucl is None else max(0.00, round(prev_lrr_ucl, 2))
    previous_month_lrr_ucl = 0.00 if prior_lrr_ucl is None else max(0.00, round(prior_lrr_ucl, 2))

    return {
        "current_month_start": current_month_start,
        "previous_month_start": previous_month_start,
        "current_month_target_lcl": current_month_target_lcl,
        "previous_month_target_lcl": previous_month_target_lcl,
        "current_month_raw_lcl": prev_lcl,
        "previous_month_raw_lcl": prior_lcl,
        "current_month_lrr_ucl": current_month_lrr_ucl,
        "previous_month_lrr_ucl": previous_month_lrr_ucl,
        "current_month_raw_lrr_ucl": prev_lrr_ucl,
        "previous_month_raw_lrr_ucl": prior_lrr_ucl,
    }


def get_fty_target_for_date(ts, limit_info: dict) -> float:
    if ts is None or not limit_info:
        return 95.00

    ts = pd.Timestamp(ts)
    current_month_start = limit_info.get("current_month_start")
    previous_month_start = limit_info.get("previous_month_start")

    if current_month_start is not None and ts >= current_month_start:
        return float(limit_info.get("current_month_target_lcl", 95.00))

    if previous_month_start is not None and current_month_start is not None:
        if previous_month_start <= ts < current_month_start:
            return float(limit_info.get("previous_month_target_lcl", 95.00))

    return float(limit_info.get("previous_month_target_lcl", 95.00))


def get_lrr_ucl_for_date(ts, limit_info: dict) -> float:
    if ts is None or not limit_info:
        return 0.00

    ts = pd.Timestamp(ts)
    current_month_start = limit_info.get("current_month_start")
    previous_month_start = limit_info.get("previous_month_start")

    if current_month_start is not None and ts >= current_month_start:
        return float(limit_info.get("current_month_lrr_ucl", 0.00))

    if previous_month_start is not None and current_month_start is not None:
        if previous_month_start <= ts < current_month_start:
            return float(limit_info.get("previous_month_lrr_ucl", 0.00))

    return float(limit_info.get("previous_month_lrr_ucl", 0.00))

# =========================================================
# L2 ANALYSIS EXPORT HELPERS
# =========================================================
L2_ANALYSIS_DIR = SHARED_ROOT / "3. L2 Analysis Plots"


def get_previous_day() -> pd.Timestamp:
    return pd.Timestamp.today().normalize() - pd.Timedelta(days=1)


def get_l2_7day_start_end() -> tuple[pd.Timestamp, pd.Timestamp]:
    end_day = get_previous_day()
    start_day = end_day - pd.Timedelta(days=6)
    return start_day, end_day


def safe_folder_name(name: str) -> str:
    return (
        str(name)
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("|", "_")
    )


def scope_has_header_data_on_date(device_code: str, station_value: str | None, target_day: pd.Timestamp) -> bool:
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)

    query = f"""
        SELECT COUNT(*) AS row_count
        FROM {HEADER_TABLE}
        {scope_filter}
          AND CAST(end_time AS DATE) = DATE '{target_day.strftime("%Y-%m-%d")}'
    """

    df = run_query(query)
    return not df.empty and int(df["row_count"].iloc[0]) > 0


def scope_has_header_data_in_range(
    device_code: str,
    station_value: str | None,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp
) -> bool:
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)

    query = f"""
        SELECT COUNT(*) AS row_count
        FROM {HEADER_TABLE}
        {scope_filter}
          AND CAST(end_time AS DATE)
              BETWEEN DATE '{start_day.strftime("%Y-%m-%d")}'
              AND DATE '{end_day.strftime("%Y-%m-%d")}'
    """

    df = run_query(query)
    return not df.empty and int(df["row_count"].iloc[0]) > 0


def format_top5_l2_text(df: pd.DataFrame, qty_col: str) -> str:
    if df is None or df.empty:
        return "No data available"

    work = df.copy()
    work[qty_col] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0)

    work = work[
        (work[qty_col] > 0) &
        (work["soft_bin"].astype(str).str.strip() != "-")
    ].copy()

    if work.empty:
        return "No data available"

    top5 = (
        work.groupby(["soft_bin", "errCode"], as_index=False)
        .agg(qty=(qty_col, "sum"))
        .sort_values(["qty", "soft_bin", "errCode"], ascending=[False, True, True])
        .head(5)
        .reset_index(drop=True)
    )

    lines = []
    for i, r in top5.iterrows():
        sb = str(r["soft_bin"]).strip()
        ec = str(r["errCode"]).strip()
        lines.append(f"#{i + 1} {sb}: {ec}")

    return "\n".join(lines)


def export_l2_analysis_txt_files(
    device_code: str,
    station_value: str | None,
    section_label: str,
    daily_summary_errcode_df: pd.DataFrame,
    row5_rpr_errcode_df: pd.DataFrame
) -> None:
    start_day, end_day = get_l2_7day_start_end()

    if not scope_has_header_data_in_range(device_code, station_value, start_day, end_day):
        return

    yesterday = get_previous_day()

    # Use section_label to avoid overwriting QX1-FUNC1 / QX1-FUNC2 / QX1-OA.
    # If you strictly want raw device_code only, replace section_label with device_code.
    device_folder = L2_ANALYSIS_DIR / safe_folder_name(section_label)
    ensure_folder(device_folder)

    defect_df = daily_summary_errcode_df.copy()
    defect_df["test_date"] = pd.to_datetime(defect_df["test_date"], errors="coerce")
    defect_df = defect_df[defect_df["test_date"].dt.date == yesterday.date()].copy()

    defect_text = format_top5_l2_text(defect_df, qty_col="fail_qty")
    (device_folder / "top5_defect_rate.txt").write_text(defect_text, encoding="utf-8")

    rpr_df = row5_rpr_errcode_df.copy()
    rpr_df["test_date"] = pd.to_datetime(rpr_df["test_date"], errors="coerce")
    rpr_df = rpr_df[rpr_df["test_date"].dt.date == yesterday.date()].copy()

    rpr_text = format_top5_l2_text(rpr_df, qty_col="recovered_qty")
    (device_folder / "top5_high_rpr_errCode.txt").write_text(rpr_text, encoding="utf-8")

# =========================================================
# UPDATED SCOPE LABELING
# =========================================================
@st.cache_data(show_spinner=False)
def get_available_dashboard_scopes() -> list[dict]:
    valid_station_sql = ",".join([f"'{s}'" for s in VALID_DASHBOARD_STATIONS])

    query = f"""
        WITH recent_data AS (
            SELECT
                TRIM(CAST(device_code AS VARCHAR)) AS device_code,
                TRIM(CAST(station AS VARCHAR)) AS station,
                CAST(end_time AS DATE) AS test_date
            FROM {HEADER_TABLE}
            WHERE CAST(end_time AS DATE) >= (
                SELECT CAST(MAX(end_time) AS DATE) - INTERVAL 27 DAY
                FROM {HEADER_TABLE}
            )
        )
        SELECT DISTINCT
            device_code,
            station
        FROM recent_data
        WHERE COALESCE(device_code, '') <> ''
          AND UPPER(COALESCE(device_code, '')) NOT LIKE 'QDM%'
          AND station IN ({valid_station_sql})
        ORDER BY device_code, station
    """

    df = run_query(query)

    scopes = []
    if df.empty:
        return scopes

    seen_QX_devices = set()

    for _, row in df.iterrows():
        device_code = str(row["device_code"]).strip()
        station = str(row["station"]).strip()

        station_label = STATION_MAP.get(station, station)

        if device_code in QX_DEVICES:
            seen_QX_devices.add(device_code)
            scopes.append({
                "device_code": device_code,
                "station": station,
                "tab_label": f"{device_code}-{station_label}",
                "section_label": f"{device_code}-{station_label}",
            })
        else:
            if station == "1000":
                tab_label = device_code
            elif station == "1004":
                tab_label = device_code
            else:
                tab_label = f"{device_code}-{station_label}"
            scopes.append({
                "device_code": device_code,
                "station": station,
                "tab_label": tab_label,
                "section_label": tab_label,
            })

    for QX_device in sorted(seen_QX_devices):
        scopes.append({
            "device_code": QX_device,
            "station": None,
            "tab_label": f"{QX_device}-OA",
            "section_label": f"{QX_device}-OA",
        })

    def scope_sort_key(x):
        device = x["device_code"]
        station = x["station"]

        station_order = {
            "1000": 0,
            "1001": 1,
            "1002": 2,
            "1004": 3,
            None: 4,
        }

        if device in QX_DEVICES:
            return (0, device, station_order.get(station, 99))

        return (1, device, station_order.get(station, 99), station or "")

    return sorted(scopes, key=scope_sort_key)

# =========================================================
# DEVICE RANKING HELPER FOR YOY / QOQ / MOM
# =========================================================
@st.cache_data(show_spinner=False)
def get_ranked_period_scopes() -> list[dict]:
    valid_station_sql = ",".join([f"'{s}'" for s in VALID_DASHBOARD_STATIONS])

    query = f"""
        WITH base AS (
            SELECT
                TRIM(CAST(device_code AS VARCHAR)) AS device_code,
                TRIM(CAST(station AS VARCHAR)) AS station,
                CAST(end_time AS DATE) AS test_date,
                COALESCE(final_pass_qty, 0) AS output_qty
            FROM {HEADER_TABLE}
            WHERE CAST(end_time AS DATE) >= (
                SELECT CAST(MAX(end_time) AS DATE) - INTERVAL 27 DAY
                FROM {HEADER_TABLE}
            )
              AND COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') <> ''
              AND UPPER(COALESCE(TRIM(CAST(device_code AS VARCHAR)), '')) NOT LIKE 'QDM%'
              AND TRIM(CAST(station AS VARCHAR)) IN ({valid_station_sql})
        )
        SELECT
            device_code,
            station,
            SUM(output_qty) AS past_month_output_qty
        FROM base
        GROUP BY device_code, station
    """

    df = run_query(query)
    if df.empty:
        return []

    work = df.copy()
    work["device_code"] = work["device_code"].astype(str).str.strip()
    work["station"] = work["station"].astype(str).str.strip()
    work["past_month_output_qty"] = pd.to_numeric(work["past_month_output_qty"], errors="coerce").fillna(0)

    regular_rows = []
    QX_family_rows = {}

    for _, row in work.iterrows():
        device_code = row["device_code"]
        station = row["station"]
        qty = float(row["past_month_output_qty"])

        if device_code in QX_DEVICES:
            QX_family_rows.setdefault(device_code, {})
            QX_family_rows[device_code][station] = qty
        else:
            regular_rows.append({
                "device_code": device_code,
                "station": station,
                "rank_qty": qty,
                "family_type": "regular",
            })

    QX_family_rank_rows = []
    for QX_device, station_qty_map in QX_family_rows.items():
        QX_family_rank_rows.append({
            "device_code": QX_device,
            "station": None,
            "rank_qty": sum(station_qty_map.values()),
            "family_type": "QX_family",
        })

    rank_seed = sorted(
        regular_rows + QX_family_rank_rows,
        key=lambda x: (-x["rank_qty"], x["device_code"], x["station"] or "")
    )

    final_scopes = []

    for item in rank_seed:
        device_code = item["device_code"]

        if item["family_type"] == "regular":
            station = item["station"]
            station_label = STATION_MAP.get(station, station)

            if station == "1000":
                tab_label = device_code
            elif station == "1004":
                tab_label = device_code
            else:
                tab_label = f"{device_code}-{station_label}"

            final_scopes.append({
                "device_code": device_code,
                "station": station,
                "tab_label": tab_label,
                "section_label": tab_label,
            })

        else:
            final_scopes.append({
                "device_code": device_code,
                "station": None,
                "tab_label": f"{device_code}-OA",
                "section_label": f"{device_code}-OA",
            })

            station_qty_map = QX_family_rows.get(device_code, {})
            for station in VALID_DASHBOARD_STATIONS:
                if station in station_qty_map:
                    station_label = STATION_MAP.get(station, station)

                    if station == "1004":
                        tab_label = device_code
                    else:
                        tab_label = f"{device_code}-{station_label}"

                    final_scopes.append({
                        "device_code": device_code,
                        "station": station,
                        "tab_label": tab_label,
                        "section_label": tab_label,
                    })

    return final_scopes

# =========================================================
# PERIOD HEADER HELPERS
# =========================================================
def get_fty_status_color(value) -> str:
    if pd.isna(value):
        return "#808080"  # gray
    v = float(value)
    if v < 98:
        return "#C00000"  # red
    elif v < 99:
        return "#BF9000"  # yellow / amber
    return "#008000"      # green


def format_delta_vs_prior(curr_val, prior_val) -> str:
    if pd.isna(curr_val) or pd.isna(prior_val):
        return "(vs prior: N/A)"
    delta = float(curr_val) - float(prior_val)
    sign = "+" if delta >= 0 else ""
    return f"({sign}{delta:.2f}% vs {prior_val:.0f if False else ''})"


def build_period_title_suffix(plot_df: pd.DataFrame, period_label: str) -> str:
    if plot_df is None or plot_df.empty:
        return "FTY @ N/A"

    latest_year = pd.Timestamp.today().year

    if period_label == "YoY":
        current_row = plot_df[plot_df["bucket_type"] == "year_running_total"].copy()
        prior_row = plot_df[plot_df["bucket_type"] == "year"].sort_values("sort_key").tail(1).copy()
        compare_label = str(latest_year - 1)
    elif period_label == "QoQ":
        current_row = plot_df[plot_df["bucket_type"] == "quarter_running_total"].copy()
        prior_row = plot_df[plot_df["bucket_type"] == "quarter"].sort_values("sort_key").tail(1).copy()
        compare_label = "prior quarter"
    elif period_label == "MoM":
        current_row = plot_df[plot_df["bucket_type"] == "month_running_total"].copy()
        prior_row = plot_df[plot_df["bucket_type"] == "month"].sort_values("sort_key").tail(1).copy()
        compare_label = "prior month"
    else:
        return "FTY @ N/A"

    curr_fty = current_row["FTY"].iloc[0] if not current_row.empty else None
    prior_fty = prior_row["FTY"].iloc[0] if not prior_row.empty else None

    if pd.isna(curr_fty):
        return f"{latest_year} FTY @ N/A"

    if pd.isna(prior_fty):
        return f"{latest_year} FTY @ {float(curr_fty):.2f}%"

    delta = float(curr_fty) - float(prior_fty)
    sign = "+" if delta >= 0 else ""
    return f"{latest_year} FTY @ {float(curr_fty):.2f}% ({sign}{delta:.2f}% vs {compare_label})"

def render_period_header(plot_df: pd.DataFrame, period_label: str, section_label: str) -> str:
    header_html = build_period_header_summary(plot_df, period_label, section_label)
    st.markdown(header_html, unsafe_allow_html=True)
    return header_html

def is_QX_oa_scope(device_code: str, station_value: str | None) -> bool:
    dc = (device_code or "").strip().upper()
    return dc in {"QX1", "QX2"} and station_value is None

# =========================================================
# UPDATED DAILY SUMMARY RAW SQL WINDOW FOR 4 WEEKS
# =========================================================
def build_excluded_retest_station_sql(
    header_station_expr: str,
    flow_expr: str
) -> str:
    """
    Exclude retest rows coming from 1010 / 1011 / 1012 across all detail-based logic.
    Applies only to RT1 / RT2 rows.
    """
    return f"""
        AND NOT (
            COALESCE(TRIM(CAST({header_station_expr} AS VARCHAR)), '') IN ('1010', '1011', '1012')
            AND COALESCE(TRIM(CAST({flow_expr} AS VARCHAR)), '') IN ('RT1', 'RT2')
        )
    """

def get_daily_summary_table_sql(device_code_value: str, station_value: str | None = None) -> str:
    if is_QX_oa_scope(device_code_value, station_value):
        device_escaped = sql_safe(device_code_value)

        unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
            device_code_value=device_code_value,
            station_value=station_value,
            sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
            err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
        )

        return f"""
            WITH scoped_detail AS (
                SELECT
                    CAST(h.end_time AS DATE) AS test_date,
                    h.schedule_no,
                    d.serial_no,
                    d.flow,
                    d.test_datetime,
                    COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                    COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                    d.pf_status
                FROM {DETAIL_TABLE} d
                INNER JOIN {HEADER_TABLE} h
                    ON {get_detail_join_condition("h", "d", station_value)}
                WHERE COALESCE(TRIM(CAST(h.device_code AS VARCHAR)), '') = '{device_escaped}'
                  AND COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  AND d.serial_no IS NOT NULL
                  AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
                  {unknown_exclSIPon_sql}
                  AND CAST(h.end_time AS DATE) BETWEEN (
                      SELECT CAST(MAX(end_time) AS DATE) - INTERVAL 27 DAY
                      FROM {HEADER_TABLE}
                      WHERE COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{device_escaped}'
                        AND COALESCE(TRIM(CAST(station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  ) AND (
                      SELECT CAST(MAX(end_time) AS DATE)
                      FROM {HEADER_TABLE}
                      WHERE COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{device_escaped}'
                        AND COALESCE(TRIM(CAST(station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  )
            ),
            input_base AS (
                SELECT
                    test_date,
                    COUNT(DISTINCT serial_no) AS input_quantity
                FROM scoped_detail
                GROUP BY test_date
            ),
            ft_first AS (
                SELECT
                    test_date,
                    schedule_no,
                    serial_no,
                    pf_status
                FROM scoped_detail
                WHERE flow = 'FT'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY test_date, schedule_no, serial_no
                    ORDER BY test_datetime ASC
                ) = 1
            ),
            first_pass_counts AS (
                SELECT
                    test_date,
                    COUNT(DISTINCT serial_no) AS first_pass_qty
                FROM ft_first
                WHERE pf_status = 'PASS'
                GROUP BY test_date
            ),
            latest_per_serial AS (
                SELECT
                    test_date,
                    schedule_no,
                    serial_no,
                    soft_bin,
                    errCode,
                    pf_status,
                    test_datetime
                FROM scoped_detail
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY test_date, schedule_no, serial_no
                    ORDER BY test_datetime DESC
                ) = 1
            ),
            daily_output AS (
                SELECT
                    test_date,
                    COUNT(*) AS latest_row_count,
                    SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) AS output_quantity,
                    ROUND(
                        100.0 * SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                        2
                    ) AS final_test_yield_pct
                FROM latest_per_serial
                GROUP BY test_date
            ),
            retest_pass_counts AS (
                SELECT
                    f.test_date,
                    COUNT(DISTINCT f.serial_no) AS retest_pass_qty
                FROM ft_first f
                INNER JOIN latest_per_serial l
                    ON f.test_date = l.test_date
                   AND f.schedule_no = l.schedule_no
                   AND f.serial_no = l.serial_no
                WHERE f.pf_status = 'FAIL'
                  AND l.pf_status = 'PASS'
                GROUP BY f.test_date
            ),
            daily_input AS (
                SELECT
                    i.test_date,
                    strftime(i.test_date, '%m/%d') AS short_date,
                    i.input_quantity,
                    ROUND(
                        100.0 * COALESCE(fp.first_pass_qty, 0) / NULLIF(i.input_quantity, 0),
                        2
                    ) AS first_pass_yield_pct,
                    ROUND(
                        100.0 * COALESCE(rp.retest_pass_qty, 0) / NULLIF(i.input_quantity, 0),
                        2
                    ) AS retest_pass_yield_pct,
                    ROUND(
                        100.0 * COALESCE(rp.retest_pass_qty, 0) / NULLIF(i.input_quantity, 0),
                        2
                    ) AS retest_rate_pct
                FROM input_base i
                LEFT JOIN first_pass_counts fp
                    ON i.test_date = fp.test_date
                LEFT JOIN retest_pass_counts rp
                    ON i.test_date = rp.test_date
            ),
            daily_mother_lot AS (
                SELECT
                    test_date,
                    string_agg(
                        DISTINCT SUBSTR(schedule_no, 1, 6),
                        '\n' ORDER BY SUBSTR(schedule_no, 1, 6)
                    ) AS mother_lot_list
                FROM scoped_detail
                GROUP BY test_date
            ),
            lot_level_yield AS (
                SELECT
                    test_date,
                    schedule_no,
                    COUNT(*) AS total_serial_count,
                    SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) AS pass_serial_count,
                    ROUND(
                        100.0 * SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                        2
                    ) AS final_yield_pct
                FROM latest_per_serial
                GROUP BY test_date, schedule_no
            ),
            lrr_detail AS (
                SELECT
                    test_date,
                    COUNT(DISTINCT CASE WHEN final_yield_pct < 98 THEN schedule_no END) AS lrr_count,
                    COUNT(DISTINCT schedule_no) AS total_lot_count,
                    string_agg(
                        DISTINCT CASE WHEN final_yield_pct < 98 THEN schedule_no END,
                        '\n' ORDER BY CASE WHEN final_yield_pct < 98 THEN schedule_no END
                    ) AS lrr_lot_list
                FROM lot_level_yield
                GROUP BY test_date
            ),
            fail_grouped AS (
                SELECT
                    l.test_date,
                    l.soft_bin,
                    MIN(l.errCode) AS errCode,
                    COUNT(*) AS fail_count,
                    ROUND(
                        100.0 * COUNT(*) / NULLIF(o.latest_row_count, 0),
                        2
                    ) AS fail_pct
                FROM latest_per_serial l
                INNER JOIN daily_output o
                    ON l.test_date = o.test_date
                WHERE l.pf_status = 'FAIL'
                GROUP BY
                    l.test_date,
                    l.soft_bin,
                    o.latest_row_count
            ),
            fail_ranked AS (
                SELECT
                    test_date,
                    soft_bin,
                    errCode,
                    fail_count,
                    fail_pct,
                    CONCAT(
                        CAST(ROUND(fail_pct, 2) AS VARCHAR),
                        '% : ',
                        COALESCE(soft_bin, 'UNKNOWN'),
                        '\n',
                        COALESCE(errCode, 'UNKNOWN')
                    ) AS softbin_pair,
                    ROW_NUMBER() OVER (
                        PARTITION BY test_date
                        ORDER BY fail_count DESC, soft_bin
                    ) AS rn
                FROM fail_grouped
            ),
            fail_pivot AS (
                SELECT
                    test_date,
                    MAX(CASE WHEN rn = 1 THEN softbin_pair END) AS top1_error,
                    MAX(CASE WHEN rn = 2 THEN softbin_pair END) AS top2_error,
                    MAX(CASE WHEN rn = 3 THEN softbin_pair END) AS top3_error,
                    MAX(CASE WHEN rn = 1 THEN fail_count END) AS top1_fail_count,
                    MAX(CASE WHEN rn = 2 THEN fail_count END) AS top2_fail_count,
                    MAX(CASE WHEN rn = 3 THEN fail_count END) AS top3_fail_count,
                    MAX(CASE WHEN rn = 1 THEN fail_pct END) AS top1_fail_pct,
                    MAX(CASE WHEN rn = 2 THEN fail_pct END) AS top2_fail_pct,
                    MAX(CASE WHEN rn = 3 THEN fail_pct END) AS top3_fail_pct,
                    COALESCE(SUM(CASE WHEN rn >= 4 THEN fail_count ELSE 0 END), 0) AS other_fail_count,
                    COALESCE(ROUND(SUM(CASE WHEN rn >= 4 THEN fail_pct ELSE 0 END), 2), 0) AS other_fail_pct
                FROM fail_ranked
                GROUP BY test_date
            )
            SELECT
                i.test_date,
                i.short_date,
                m.mother_lot_list,
                i.input_quantity,
                COALESCE(o.output_quantity, 0) AS output_quantity,
                i.first_pass_yield_pct,
                COALESCE(o.final_test_yield_pct, 0) AS final_test_yield_pct,
                i.retest_pass_yield_pct,
                i.retest_rate_pct,
                ROUND(100.0 * l.lrr_count / NULLIF(l.total_lot_count, 0), 2) AS lrr_pct,
                l.lrr_count,
                l.total_lot_count,
                COALESCE(l.lrr_lot_list, '-') AS lrr_lot_list,
                COALESCE(f.top1_error, '-') AS top1_error,
                COALESCE(f.top2_error, '-') AS top2_error,
                COALESCE(f.top3_error, '-') AS top3_error,
                CASE
                    WHEN COALESCE(f.other_fail_count, 0) = 0 THEN '-'
                    ELSE CONCAT(
                        CAST(ROUND(COALESCE(f.other_fail_pct, 0), 2) AS VARCHAR),
                        '% : Other soft_bins'
                    )
                END AS other_errorcodes,
                COALESCE(f.top1_fail_count, 0) AS top1_fail_count,
                COALESCE(f.top2_fail_count, 0) AS top2_fail_count,
                COALESCE(f.top3_fail_count, 0) AS top3_fail_count,
                COALESCE(f.top1_fail_pct, 0) AS top1_fail_pct,
                COALESCE(f.top2_fail_pct, 0) AS top2_fail_pct,
                COALESCE(f.top3_fail_pct, 0) AS top3_fail_pct,
                COALESCE(f.other_fail_count, 0) AS other_fail_count,
                COALESCE(f.other_fail_pct, 0) AS other_fail_pct
            FROM daily_input i
            LEFT JOIN daily_output o
                ON i.test_date = o.test_date
            LEFT JOIN daily_mother_lot m
                ON i.test_date = m.test_date
            LEFT JOIN lrr_detail l
                ON i.test_date = l.test_date
            LEFT JOIN fail_pivot f
                ON i.test_date = f.test_date
            ORDER BY i.test_date
        """

    filters = [f"device_code = '{sql_safe(device_code_value)}'"]

    if station_value is not None:
        filters.append(f"station = '{sql_safe(station_value)}'")
    else:
        filters.append("station NOT IN ('1010','1011','1012')")

    where_sql = "WHERE " + " AND ".join(filters)

    unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
        device_code_value=device_code_value,
        station_value=station_value,
        sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
        err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
    )

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {where_sql}
        ),
        max_day AS (
            SELECT CAST(MAX(end_time) AS DATE) AS latest_day
            FROM scoped_header
        ),
        latest_header_per_lot AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_code, station, schedule_no, CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
                WHERE CAST(end_time AS DATE) BETWEEN (
                    SELECT latest_day - INTERVAL 27 DAY FROM max_day
                ) AND (
                    SELECT latest_day FROM max_day
                )
            ) x
            WHERE rn = 1
        ),
        base AS (
            SELECT
                device_code,
                station,
                schedule_no,
                SUBSTR(schedule_no, 1, 6) AS mother_lot,
                CAST(end_time AS DATE) AS test_date,
                STRFTIME(CAST(end_time AS DATE), '%m/%d') AS short_date,
                COALESCE(input_quantity, 0) AS input_quantity,
                COALESCE(first_pass_qty, 0) AS first_pass_qty,
                COALESCE(final_pass_qty, 0) AS final_pass_qty,
                COALESCE(retest_pass_qty, 0) AS retest_pass_qty,
                COALESCE(retest_rate_pct, 0) AS retest_rate_pct,
                COALESCE(final_yield_pct, 0) AS final_yield_pct,
                file_hash
            FROM latest_header_per_lot
        ),
        daily_input AS (
            SELECT
                test_date,
                strftime(test_date, '%m/%d') AS short_date,
                SUM(input_quantity) AS input_quantity,
                ROUND(
                    100.0 * SUM(first_pass_qty) / NULLIF(SUM(input_quantity), 0),
                    2
                ) AS first_pass_yield_pct,
                ROUND(
                    100.0 * SUM(retest_pass_qty) / NULLIF(SUM(input_quantity), 0),
                    2
                ) AS retest_pass_yield_pct,
                ROUND(
                    SUM(input_quantity * COALESCE(retest_rate_pct, 0))
                    / NULLIF(SUM(input_quantity), 0),
                    2
                ) AS retest_rate_pct
            FROM base
            GROUP BY test_date
        ),
        daily_mother_lot AS (
            SELECT
                test_date,
                string_agg(DISTINCT mother_lot, '\n' ORDER BY mother_lot) AS mother_lot_list
            FROM base
            GROUP BY test_date
        ),
        lrr_detail AS (
            SELECT
                test_date,
                COUNT(DISTINCT CASE WHEN final_yield_pct < 98 THEN schedule_no END) AS lrr_count,
                COUNT(DISTINCT schedule_no) AS total_lot_count,
                string_agg(
                    DISTINCT CASE WHEN final_yield_pct < 98 THEN schedule_no END,
                    '\n' ORDER BY CASE WHEN final_yield_pct < 98 THEN schedule_no END
                ) AS lrr_lot_list
            FROM base
            GROUP BY test_date
        ),
        kept_files AS (
            SELECT file_hash
            FROM latest_header_per_lot
        ),
        error_base AS (
            SELECT
                CAST(h.end_time AS DATE) AS test_date,
                h.schedule_no,
                d.serial_no,
                COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            WHERE d.file_hash IN (SELECT file_hash FROM kept_files)
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
              {unknown_exclSIPon_sql}
        ),
        latest_per_serial AS (
            SELECT
                test_date,
                schedule_no,
                serial_no,
                soft_bin,
                errCode,
                pf_status,
                test_datetime
            FROM error_base
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime DESC
            ) = 1
        ),
        daily_output AS (
            SELECT
                l.test_date,
                COUNT(*) AS latest_row_count,
                SUM(CASE WHEN l.pf_status = 'PASS' THEN 1 ELSE 0 END) AS output_quantity,
                ROUND(
                    100.0 * SUM(CASE WHEN l.pf_status = 'PASS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                    2
                ) AS final_test_yield_pct
            FROM latest_per_serial l
            GROUP BY l.test_date
        ),
        fail_grouped AS (
            SELECT
                l.test_date,
                l.soft_bin,
                MIN(l.errCode) AS errCode,
                COUNT(*) AS fail_count,
                ROUND(
                    100.0 * COUNT(*) / NULLIF(o.latest_row_count, 0),
                    2
                ) AS fail_pct
            FROM latest_per_serial l
            INNER JOIN daily_output o
                ON l.test_date = o.test_date
            WHERE l.pf_status = 'FAIL'
            GROUP BY
                l.test_date,
                l.soft_bin,
                o.latest_row_count
        ),
        fail_ranked AS (
            SELECT
                test_date,
                soft_bin,
                errCode,
                fail_count,
                fail_pct,
                CONCAT(
                    CAST(ROUND(fail_pct, 2) AS VARCHAR),
                    '% : ',
                    COALESCE(soft_bin, 'UNKNOWN'),
                    '\n',
                    COALESCE(errCode, 'UNKNOWN')
                ) AS softbin_pair,
                ROW_NUMBER() OVER (
                    PARTITION BY test_date
                    ORDER BY fail_count DESC, soft_bin
                ) AS rn
            FROM fail_grouped
        ),
        fail_pivot AS (
            SELECT
                test_date,
                MAX(CASE WHEN rn = 1 THEN softbin_pair END) AS top1_error,
                MAX(CASE WHEN rn = 2 THEN softbin_pair END) AS top2_error,
                MAX(CASE WHEN rn = 3 THEN softbin_pair END) AS top3_error,
                MAX(CASE WHEN rn = 1 THEN fail_count END) AS top1_fail_count,
                MAX(CASE WHEN rn = 2 THEN fail_count END) AS top2_fail_count,
                MAX(CASE WHEN rn = 3 THEN fail_count END) AS top3_fail_count,
                MAX(CASE WHEN rn = 1 THEN fail_pct END) AS top1_fail_pct,
                MAX(CASE WHEN rn = 2 THEN fail_pct END) AS top2_fail_pct,
                MAX(CASE WHEN rn = 3 THEN fail_pct END) AS top3_fail_pct,
                COALESCE(SUM(CASE WHEN rn >= 4 THEN fail_count ELSE 0 END), 0) AS other_fail_count,
                COALESCE(ROUND(SUM(CASE WHEN rn >= 4 THEN fail_pct ELSE 0 END), 2), 0) AS other_fail_pct
            FROM fail_ranked
            GROUP BY test_date
        )
        SELECT
            i.test_date,
            i.short_date,
            m.mother_lot_list,
            i.input_quantity,
            COALESCE(o.output_quantity, 0) AS output_quantity,
            i.first_pass_yield_pct,
            COALESCE(o.final_test_yield_pct, 0) AS final_test_yield_pct,
            i.retest_pass_yield_pct,
            i.retest_rate_pct,
            ROUND(100.0 * l.lrr_count / NULLIF(l.total_lot_count, 0), 2) AS lrr_pct,
            l.lrr_count,
            l.total_lot_count,
            COALESCE(l.lrr_lot_list, '-') AS lrr_lot_list,
            COALESCE(f.top1_error, '-') AS top1_error,
            COALESCE(f.top2_error, '-') AS top2_error,
            COALESCE(f.top3_error, '-') AS top3_error,
            CASE
                WHEN COALESCE(f.other_fail_count, 0) = 0 THEN '-'
                ELSE CONCAT(
                    CAST(ROUND(COALESCE(f.other_fail_pct, 0), 2) AS VARCHAR),
                    '% : Other soft_bins'
                )
            END AS other_errorcodes,
            COALESCE(f.top1_fail_count, 0) AS top1_fail_count,
            COALESCE(f.top2_fail_count, 0) AS top2_fail_count,
            COALESCE(f.top3_fail_count, 0) AS top3_fail_count,
            COALESCE(f.top1_fail_pct, 0) AS top1_fail_pct,
            COALESCE(f.top2_fail_pct, 0) AS top2_fail_pct,
            COALESCE(f.top3_fail_pct, 0) AS top3_fail_pct,
            COALESCE(f.other_fail_count, 0) AS other_fail_count,
            COALESCE(f.other_fail_pct, 0) AS other_fail_pct
        FROM daily_input i
        LEFT JOIN daily_output o
            ON i.test_date = o.test_date
        LEFT JOIN daily_mother_lot m
            ON i.test_date = m.test_date
        LEFT JOIN lrr_detail l
            ON i.test_date = l.test_date
        LEFT JOIN fail_pivot f
            ON i.test_date = f.test_date
        ORDER BY i.test_date
    """
# =========================================================
# NEW ROW - MOTHER LOT YIELD TREND SQL
# =========================================================

def get_mother_lot_yield_sql(device_code: str, station_value: str | None) -> str:
    # scope_filter = make_scope_filter_sql(device_code, station_value)
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)
    exclude_retest_filter = "AND d.station NOT IN ('1010','1011','1012')"

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {scope_filter}
        ),
        latest_header_per_mother_lot_day AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    LEFT(TRIM(CAST(schedule_no AS VARCHAR)), 6) AS mother_lot,
                    CAST(end_time AS DATE) AS test_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            device_code,
                            station,
                            LEFT(TRIM(CAST(schedule_no AS VARCHAR)), 6),
                            CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
                WHERE schedule_no IS NOT NULL
                  AND TRIM(CAST(schedule_no AS VARCHAR)) <> ''
                  AND LEFT(TRIM(CAST(schedule_no AS VARCHAR)), 6) LIKE '%XU%'
            ) x
            WHERE rn = 1
        )
        SELECT
            mother_lot,
            test_date,
            SUM(COALESCE(input_quantity, 0)) AS input_quantity,
            SUM(COALESCE(first_pass_qty, 0)) AS first_pass_qty,
            SUM(COALESCE(final_pass_qty, 0)) AS final_pass_qty,
            SUM(COALESCE(retest_pass_qty, 0)) AS retest_pass_qty,
            ROUND(
                100.0 * SUM(COALESCE(first_pass_qty, 0)) / NULLIF(SUM(COALESCE(input_quantity, 0)), 0),
                2
            ) AS first_pass_yield_pct,
            ROUND(
                100.0 * SUM(COALESCE(final_pass_qty, 0)) / NULLIF(SUM(COALESCE(input_quantity, 0)), 0),
                2
            ) AS final_yield_pct
        FROM latest_header_per_mother_lot_day
        GROUP BY mother_lot, test_date
        ORDER BY test_date, mother_lot
    """
# =========================================================
# NEW ROW - PER LOT (SCHEDULE NO) YIELD TREND SQL
# =========================================================
def get_schedule_no_yield_sql(device_code: str, station_value: str | None) -> str:
    # scope_filter = make_scope_filter_sql(device_code, station_value)
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)
    exclude_retest_filter = "AND d.station NOT IN ('1010','1011','1012')"

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {scope_filter}
        ),
        latest_header_per_schedule_day AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    TRIM(CAST(schedule_no AS VARCHAR)) AS schedule_no_clean,
                    CAST(end_time AS DATE) AS test_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            device_code,
                            station,
                            TRIM(CAST(schedule_no AS VARCHAR)),
                            CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
                WHERE schedule_no IS NOT NULL
                  AND TRIM(CAST(schedule_no AS VARCHAR)) <> ''
            ) x
            WHERE rn = 1
        )
        SELECT
            schedule_no_clean AS schedule_no,
            test_date,
            SUM(COALESCE(input_quantity, 0)) AS input_quantity,
            SUM(COALESCE(first_pass_qty, 0)) AS first_pass_qty,
            SUM(COALESCE(final_pass_qty, 0)) AS final_pass_qty,
            SUM(COALESCE(retest_pass_qty, 0)) AS retest_pass_qty,
            ROUND(
                100.0 * SUM(COALESCE(first_pass_qty, 0)) / NULLIF(SUM(COALESCE(input_quantity, 0)), 0),
                2
            ) AS first_pass_yield_pct,
            ROUND(
                100.0 * SUM(COALESCE(final_pass_qty, 0)) / NULLIF(SUM(COALESCE(input_quantity, 0)), 0),
                2
            ) AS final_yield_pct
        FROM latest_header_per_schedule_day
        GROUP BY schedule_no_clean, test_date
        ORDER BY test_date, schedule_no_clean
    """

def get_station_period_trend_sql(device_code: str, station_value: str | None) -> str:
    # scope_filter = make_scope_filter_sql(device_code, station_value)
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)
    exclude_retest_filter = "AND d.station NOT IN ('1010','1011','1012')"
    excluded_retest_station_sql = build_excluded_retest_station_sql(
        header_station_expr="h.station",
        flow_expr="d.flow"
    )

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {scope_filter}
              AND CAST(end_time AS DATE) >= DATE '{YoY_PERIOD_MIN_DATE}'
        ),
        latest_header_per_lot AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_code, station, schedule_no, CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
            ) x
            WHERE rn = 1
        ),
        daily_header AS (
            SELECT
                CAST(end_time AS DATE) AS test_date,
                STRFTIME(CAST(end_time AS DATE), '%m/%d') AS short_date,
                SUM(COALESCE(input_quantity, 0)) AS input_quantity,
                SUM(COALESCE(first_pass_qty, 0)) AS first_pass_qty,
                SUM(COALESCE(final_pass_qty, 0)) AS final_pass_qty,
                SUM(COALESCE(retest_pass_qty, 0)) AS retest_pass_qty
            FROM latest_header_per_lot
            GROUP BY CAST(end_time AS DATE)
        ),
        kept_files AS (
            SELECT file_hash
            FROM latest_header_per_lot
        ),
        detail_joined AS (
            SELECT
                CAST(h.end_time AS DATE) AS test_date,
                h.schedule_no,
                d.serial_no,
                d.flow,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            WHERE d.file_hash IN (SELECT file_hash FROM kept_files)
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
        ),
        ft_first AS (
            SELECT
                test_date,
                schedule_no,
                serial_no,
                pf_status
            FROM detail_joined
            WHERE flow = 'FT'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime ASC
            ) = 1
        ),
        rt1_pass AS (
            SELECT DISTINCT
                test_date,
                schedule_no,
                serial_no
            FROM detail_joined
            WHERE flow = 'RT1'
              AND pf_status = 'PASS'
        ),
        daily_rt1_output AS (
            SELECT
                f.test_date,
                COUNT(DISTINCT CASE
                    WHEN f.pf_status = 'PASS' OR r.serial_no IS NOT NULL
                    THEN f.serial_no
                END) AS rt1_pass_qty
            FROM ft_first f
            LEFT JOIN rt1_pass r
                ON f.test_date = r.test_date
               AND f.schedule_no = r.schedule_no
               AND f.serial_no = r.serial_no
            GROUP BY f.test_date
        )
        SELECT
            h.test_date,
            h.short_date,
            h.input_quantity,
            h.first_pass_qty,
            COALESCE(r.rt1_pass_qty, 0) AS rt1_pass_qty,
            h.final_pass_qty,
            h.retest_pass_qty,
            ROUND(100.0 * h.first_pass_qty / NULLIF(h.input_quantity, 0), 2) AS first_pass_yield_pct,
            ROUND(100.0 * COALESCE(r.rt1_pass_qty, 0) / NULLIF(h.input_quantity, 0), 2) AS rt1_yield_pct,
            ROUND(100.0 * h.final_pass_qty / NULLIF(h.input_quantity, 0), 2) AS final_yield_pct,
            ROUND(100.0 * h.retest_pass_qty / NULLIF(h.input_quantity, 0), 2) AS retest_rate_pct
        FROM daily_header h
        LEFT JOIN daily_rt1_output r
            ON h.test_date = r.test_date
        ORDER BY h.test_date
    """

def get_daily_summary_errcode_period_sql(device_code_value: str, station_value: str | None = None) -> str:
    filters = [
        f"COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{sql_safe(device_code_value)}'"
    ]

    if station_value is not None:
        filters.append(
            f"COALESCE(TRIM(CAST(station AS VARCHAR)), '') = '{sql_safe(station_value)}'"
        )

    where_sql = "WHERE " + " AND ".join(filters) + f" AND CAST(end_time AS DATE) >= DATE '{YoY_PERIOD_MIN_DATE}'"

    unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
        device_code_value=device_code_value,
        station_value=station_value,
        sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
        err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
    )

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {where_sql}
        ),
        latest_header_per_lot AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_code, station, schedule_no, CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
            ) x
            WHERE rn = 1
        ),
        kept_files AS (
            SELECT file_hash
            FROM latest_header_per_lot
        ),
        detail_joined AS (
            SELECT
                CAST(h.end_time AS DATE) AS test_date,
                h.schedule_no,
                d.serial_no,
                d.test_datetime,
                COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                d.pf_status
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            WHERE d.file_hash IN (SELECT file_hash FROM kept_files)
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
              {unknown_exclSIPon_sql}
        ),
        latest_per_serial AS (
            SELECT
                test_date,
                schedule_no,
                serial_no,
                soft_bin,
                errCode,
                pf_status
            FROM detail_joined
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime DESC
            ) = 1
        ),
        daily_output AS (
            SELECT
                test_date,
                COUNT(*) AS latest_row_count,
                SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) AS output_quantity,
                ROUND(
                    100.0 * SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                    2
                ) AS final_test_yield_pct
            FROM latest_per_serial
            GROUP BY test_date
        ),
        fail_grouped AS (
            SELECT
                test_date,
                soft_bin,
                errCode,
                COUNT(*) AS fail_qty
            FROM latest_per_serial
            WHERE pf_status = 'FAIL'
            GROUP BY test_date, soft_bin, errCode
        )
        SELECT
            o.test_date,
            STRFTIME(o.test_date, '%m/%d') AS short_date,
            o.latest_row_count,
            o.output_quantity,
            o.final_test_yield_pct,
            COALESCE(f.soft_bin, '-') AS soft_bin,
            COALESCE(f.errCode, '-') AS errCode,
            COALESCE(f.fail_qty, 0) AS fail_qty
        FROM daily_output o
        LEFT JOIN fail_grouped f
            ON o.test_date = f.test_date
        ORDER BY o.test_date, fail_qty DESC, soft_bin, errCode
    """
# =========================================================
# UPDATED SQL - ROW 1 SOURCE
# =========================================================
def get_station_daily_trend_sql(device_code: str, station_value: str | None) -> str:
    if is_QX_oa_scope(device_code, station_value):
        device_escaped = sql_safe(device_code)

        excluded_retest_station_sql = build_excluded_retest_station_sql(
            header_station_expr="h.station",
            flow_expr="d.flow"
        )

        return f"""
            WITH scoped_detail AS (
                SELECT
                    CAST(h.end_time AS DATE) AS test_date,
                    h.schedule_no,
                    d.serial_no,
                    d.flow,
                    d.pf_status,
                    d.test_datetime
                FROM {DETAIL_TABLE} d
                INNER JOIN {HEADER_TABLE} h
                    ON {get_detail_join_condition("h", "d", station_value)}
                WHERE COALESCE(TRIM(CAST(h.device_code AS VARCHAR)), '') = '{device_escaped}'
                  AND COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  AND d.serial_no IS NOT NULL
                  AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
                  {excluded_retest_station_sql}
                  AND CAST(h.end_time AS DATE) BETWEEN (
                      SELECT CAST(MAX(end_time) AS DATE) - INTERVAL 27 DAY
                      FROM {HEADER_TABLE}
                      WHERE COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{device_escaped}'
                        AND COALESCE(TRIM(CAST(station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  ) AND (
                      SELECT CAST(MAX(end_time) AS DATE)
                      FROM {HEADER_TABLE}
                      WHERE COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{device_escaped}'
                        AND COALESCE(TRIM(CAST(station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  )
            ),
            input_base AS (
                SELECT
                    test_date,
                    COUNT(DISTINCT serial_no) AS input_quantity
                FROM scoped_detail
                GROUP BY test_date
            ),
            ft_first AS (
                SELECT
                    test_date,
                    schedule_no,
                    serial_no,
                    pf_status
                FROM scoped_detail
                WHERE flow = 'FT'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY test_date, schedule_no, serial_no
                    ORDER BY test_datetime ASC
                ) = 1
            ),
            first_pass_counts AS (
                SELECT
                    test_date,
                    COUNT(DISTINCT serial_no) AS first_pass_qty
                FROM ft_first
                WHERE pf_status = 'PASS'
                GROUP BY test_date
            ),
            latest_all AS (
                SELECT
                    test_date,
                    schedule_no,
                    serial_no,
                    pf_status
                FROM scoped_detail
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY test_date, schedule_no, serial_no
                    ORDER BY test_datetime DESC
                ) = 1
            ),
            final_pass_counts AS (
                SELECT
                    test_date,
                    COUNT(DISTINCT serial_no) AS final_pass_qty
                FROM latest_all
                WHERE pf_status = 'PASS'
                GROUP BY test_date
            ),
            retest_pass_counts AS (
                SELECT
                    f.test_date,
                    COUNT(DISTINCT f.serial_no) AS retest_pass_qty
                FROM ft_first f
                INNER JOIN latest_all l
                    ON f.test_date = l.test_date
                   AND f.schedule_no = l.schedule_no
                   AND f.serial_no = l.serial_no
                WHERE f.pf_status = 'FAIL'
                  AND l.pf_status = 'PASS'
                GROUP BY f.test_date
            ),
            daily_rt1_output AS (
                SELECT
                    f.test_date,
                    COUNT(DISTINCT CASE
                        WHEN f.pf_status = 'PASS' OR l.pf_status = 'PASS'
                        THEN f.serial_no
                    END) AS rt1_pass_qty
                FROM ft_first f
                LEFT JOIN latest_all l
                    ON f.test_date = l.test_date
                   AND f.schedule_no = l.schedule_no
                   AND f.serial_no = l.serial_no
                GROUP BY f.test_date
            )
            SELECT
                i.test_date,
                strftime(i.test_date, '%m/%d') AS short_date,
                i.input_quantity,
                COALESCE(fp.first_pass_qty, 0) AS first_pass_qty,
                COALESCE(r.rt1_pass_qty, 0) AS rt1_pass_qty,
                COALESCE(fin.final_pass_qty, 0) AS final_pass_qty,
                COALESCE(rp.retest_pass_qty, 0) AS retest_pass_qty,
                ROUND(100.0 * COALESCE(fp.first_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS first_pass_yield_pct,
                ROUND(100.0 * COALESCE(r.rt1_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS rt1_yield_pct,
                ROUND(100.0 * COALESCE(fin.final_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS final_yield_pct,
                ROUND(100.0 * COALESCE(rp.retest_pass_qty, 0) / NULLIF(i.input_quantity, 0), 2) AS retest_rate_pct
            FROM input_base i
            LEFT JOIN first_pass_counts fp
                ON i.test_date = fp.test_date
            LEFT JOIN final_pass_counts fin
                ON i.test_date = fin.test_date
            LEFT JOIN retest_pass_counts rp
                ON i.test_date = rp.test_date
            LEFT JOIN daily_rt1_output r
                ON i.test_date = r.test_date
            ORDER BY i.test_date
        """

    # scope_filter = make_scope_filter_sql(device_code, station_value)
    scope_filter = make_scope_filter_sql_excluding_101x(device_code, station_value)
    exclude_retest_filter = "AND d.station NOT IN ('1010','1011','1012')"

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {scope_filter}
        ),
        max_day AS (
            SELECT CAST(MAX(end_time) AS DATE) AS latest_day
            FROM scoped_header
        ),
        latest_header_per_lot AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_code, station, schedule_no, CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
                WHERE CAST(end_time AS DATE) BETWEEN
                    (SELECT latest_day - INTERVAL 27 DAY FROM max_day)
                    AND
                    (SELECT latest_day FROM max_day)
            ) x
            WHERE rn = 1
        ),
        daily_header AS (
            SELECT
                CAST(end_time AS DATE) AS test_date,
                STRFTIME(CAST(end_time AS DATE), '%m/%d') AS short_date,
                SUM(COALESCE(input_quantity, 0)) AS input_quantity,
                SUM(COALESCE(first_pass_qty, 0)) AS first_pass_qty,
                SUM(COALESCE(final_pass_qty, 0)) AS final_pass_qty,
                SUM(COALESCE(retest_pass_qty, 0)) AS retest_pass_qty
            FROM latest_header_per_lot
            GROUP BY CAST(end_time AS DATE)
        ),
        kept_files AS (
            SELECT file_hash
            FROM latest_header_per_lot
        ),
        detail_joined AS (
            SELECT
                CAST(h.end_time AS DATE) AS test_date,
                h.schedule_no,
                d.serial_no,
                d.flow,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            WHERE d.file_hash IN (SELECT file_hash FROM kept_files)
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
        ),
        ft_first AS (
            SELECT
                test_date,
                schedule_no,
                serial_no,
                pf_status
            FROM detail_joined
            WHERE flow = 'FT'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime ASC
            ) = 1
        ),
        rt1_pass AS (
            SELECT DISTINCT
                test_date,
                schedule_no,
                serial_no
            FROM detail_joined
            WHERE flow = 'RT1'
              AND pf_status = 'PASS'
        ),
        daily_rt1_output AS (
            SELECT
                f.test_date,
                COUNT(DISTINCT CASE
                    WHEN f.pf_status = 'PASS' OR r.serial_no IS NOT NULL
                    THEN f.serial_no
                END) AS rt1_pass_qty
            FROM ft_first f
            LEFT JOIN rt1_pass r
                ON f.test_date = r.test_date
               AND f.schedule_no = r.schedule_no
               AND f.serial_no = r.serial_no
            GROUP BY f.test_date
        )
        SELECT
            h.test_date,
            h.short_date,
            h.input_quantity,
            h.first_pass_qty,
            COALESCE(r.rt1_pass_qty, 0) AS rt1_pass_qty,
            h.final_pass_qty,
            h.retest_pass_qty,
            ROUND(100.0 * h.first_pass_qty / NULLIF(h.input_quantity, 0), 2) AS first_pass_yield_pct,
            ROUND(100.0 * COALESCE(r.rt1_pass_qty, 0) / NULLIF(h.input_quantity, 0), 2) AS rt1_yield_pct,
            ROUND(100.0 * h.final_pass_qty / NULLIF(h.input_quantity, 0), 2) AS final_yield_pct,
            ROUND(100.0 * h.retest_pass_qty / NULLIF(h.input_quantity, 0), 2) AS retest_rate_pct
        FROM daily_header h
        LEFT JOIN daily_rt1_output r
            ON h.test_date = r.test_date
        ORDER BY h.test_date
    """
# =========================================================
# ROW 2 SQL
# =========================================================
def get_daily_summary_errcode_chart_sql(device_code_value: str, station_value: str | None = None) -> str:
    if is_QX_oa_scope(device_code_value, station_value):
        device_escaped = sql_safe(device_code_value)

        unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
            device_code_value=device_code_value,
            station_value=station_value,
            sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
            err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
        )

        return f"""
            WITH scoped_detail AS (
                SELECT
                    CAST(h.end_time AS DATE) AS test_date,
                    h.schedule_no,
                    d.serial_no,
                    d.test_datetime,
                    COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                    COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                    d.pf_status
                FROM {DETAIL_TABLE} d
                INNER JOIN {HEADER_TABLE} h
                    ON {get_detail_join_condition("h", "d", station_value)}
                WHERE COALESCE(TRIM(CAST(h.device_code AS VARCHAR)), '') = '{device_escaped}'
                  AND COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  AND d.serial_no IS NOT NULL
                  AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
                  {unknown_exclSIPon_sql}
                  AND CAST(h.end_time AS DATE) BETWEEN (
                      SELECT CAST(MAX(end_time) AS DATE) - INTERVAL 27 DAY
                      FROM {HEADER_TABLE}
                      WHERE COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{device_escaped}'
                        AND COALESCE(TRIM(CAST(station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  ) AND (
                      SELECT CAST(MAX(end_time) AS DATE)
                      FROM {HEADER_TABLE}
                      WHERE COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{device_escaped}'
                        AND COALESCE(TRIM(CAST(station AS VARCHAR)), '') IN ('1000','1001','1002','1004')
                  )
            ),
            latest_per_serial AS (
                SELECT
                    test_date,
                    schedule_no,
                    serial_no,
                    soft_bin,
                    errCode,
                    pf_status
                FROM scoped_detail
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY test_date, schedule_no, serial_no
                    ORDER BY test_datetime DESC
                ) = 1
            ),
            daily_output AS (
                SELECT
                    test_date,
                    COUNT(*) AS latest_row_count,
                    SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) AS output_quantity,
                    ROUND(
                        100.0 * SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                        2
                    ) AS final_test_yield_pct
                FROM latest_per_serial
                GROUP BY test_date
            ),
            fail_grouped AS (
                SELECT
                    test_date,
                    soft_bin,
                    errCode,
                    COUNT(*) AS fail_qty
                FROM latest_per_serial
                WHERE pf_status = 'FAIL'
                GROUP BY test_date, soft_bin, errCode
            )
            SELECT
                o.test_date,
                STRFTIME(o.test_date, '%m/%d') AS short_date,
                o.latest_row_count,
                o.output_quantity,
                o.final_test_yield_pct,
                COALESCE(f.soft_bin, '-') AS soft_bin,
                COALESCE(f.errCode, '-') AS errCode,
                COALESCE(f.fail_qty, 0) AS fail_qty
            FROM daily_output o
            LEFT JOIN fail_grouped f
                ON o.test_date = f.test_date
            ORDER BY o.test_date, fail_qty DESC, soft_bin, errCode
        """

    filters = [
        f"COALESCE(TRIM(CAST(device_code AS VARCHAR)), '') = '{sql_safe(device_code_value)}'"
    ]

    if station_value is not None:
        filters.append(
            f"COALESCE(TRIM(CAST(station AS VARCHAR)), '') = '{sql_safe(station_value)}'"
        )

    where_sql = "WHERE " + " AND ".join(filters)

    unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
        device_code_value=device_code_value,
        station_value=station_value,
        sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
        err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
    )

    return f"""
        WITH scoped_header AS (
            SELECT *
            FROM {HEADER_TABLE}
            {where_sql}
        ),
        max_day AS (
            SELECT CAST(MAX(end_time) AS DATE) AS latest_day
            FROM scoped_header
        ),
        latest_header_per_lot AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_code, station, schedule_no, CAST(end_time AS DATE)
                        ORDER BY
                            end_time DESC NULLS LAST,
                            source_modified_time DESC NULLS LAST,
                            file_hash DESC
                    ) AS rn
                FROM scoped_header
                WHERE CAST(end_time AS DATE) BETWEEN (
                    SELECT latest_day - INTERVAL 27 DAY FROM max_day
                ) AND (
                    SELECT latest_day FROM max_day
                )
            ) x
            WHERE rn = 1
        ),
        kept_files AS (
            SELECT file_hash
            FROM latest_header_per_lot
        ),
        detail_joined AS (
            SELECT
                CAST(h.end_time AS DATE) AS test_date,
                h.schedule_no,
                d.serial_no,
                d.test_datetime,
                COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                d.pf_status
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            WHERE d.file_hash IN (SELECT file_hash FROM kept_files)
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
              {unknown_exclSIPon_sql}
        ),
        latest_per_serial AS (
            SELECT
                test_date,
                schedule_no,
                serial_no,
                soft_bin,
                errCode,
                pf_status
            FROM detail_joined
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime DESC
            ) = 1
        ),
        daily_output AS (
            SELECT
                test_date,
                COUNT(*) AS latest_row_count,
                SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) AS output_quantity,
                ROUND(
                    100.0 * SUM(CASE WHEN pf_status = 'PASS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                    2
                ) AS final_test_yield_pct
            FROM latest_per_serial
            GROUP BY test_date
        ),
        fail_grouped AS (
            SELECT
                test_date,
                soft_bin,
                errCode,
                COUNT(*) AS fail_qty
            FROM latest_per_serial
            WHERE pf_status = 'FAIL'
            GROUP BY test_date, soft_bin, errCode
        )
        SELECT
            o.test_date,
            STRFTIME(o.test_date, '%m/%d') AS short_date,
            o.latest_row_count,
            o.output_quantity,
            o.final_test_yield_pct,
            COALESCE(f.soft_bin, '-') AS soft_bin,
            COALESCE(f.errCode, '-') AS errCode,
            COALESCE(f.fail_qty, 0) AS fail_qty
        FROM daily_output o
        LEFT JOIN fail_grouped f
            ON o.test_date = f.test_date
        ORDER BY o.test_date, fail_qty DESC, soft_bin, errCode
    """

# =========================================================
# ROW 5 SQL - RECOVERY RATE
# =========================================================
    where_sql = "WHERE " + " AND ".join(filters)

    unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
        device_code_value=device_code_value,
        station_value=station_value,
        sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
        err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
    )

    excluded_retest_station_sql = build_excluded_retest_station_sql(
        header_station_expr="h.station",
        flow_expr="d.flow"
    )

    return f"""
        WITH joined_data AS (
            SELECT
                h.device_code,
                h.station,
                h.schedule_no,
                CAST(h.end_time AS DATE) AS test_date,
                d.serial_no,
                d.flow,
                COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            {where_sql}
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
            {unknown_exclSIPon_sql}
            {excluded_retest_station_sql}
        ),
        max_day AS (
            SELECT MAX(test_date) AS latest_day
            FROM joined_data
        ),
        base AS (
            SELECT *
            FROM joined_data
            WHERE test_date BETWEEN
                (SELECT latest_day - INTERVAL 27 DAY FROM max_day)
                AND
                (SELECT latest_day FROM max_day)
        ),
        input_base AS (
            SELECT
                test_date,
                COUNT(DISTINCT serial_no) AS total_input_qty
            FROM base
            GROUP BY test_date
        ),
        ft_first AS (
            SELECT *
            FROM base
            WHERE flow = 'FT'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime ASC
            ) = 1
        ),
        final_latest AS (
            SELECT *
            FROM base
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime DESC
            ) = 1
        ),
        ft_fail_by_err AS (
            SELECT
                f.test_date,
                f.soft_bin,
                f.errCode,
                COUNT(DISTINCT f.serial_no) AS ft_fail_qty
            FROM ft_first f
            WHERE f.pf_status = 'FAIL'
            GROUP BY f.test_date, f.soft_bin, f.errCode
        ),
        recovered_by_err AS (
            SELECT
                f.test_date,
                f.soft_bin,
                f.errCode,
                COUNT(DISTINCT f.serial_no) AS recovered_qty
            FROM ft_first f
            INNER JOIN final_latest l
                ON f.test_date = l.test_date
               AND f.schedule_no = l.schedule_no
               AND f.serial_no = l.serial_no
            WHERE f.pf_status = 'FAIL'
              AND l.pf_status = 'PASS'
            GROUP BY f.test_date, f.soft_bin, f.errCode
        )
        SELECT
            x.test_date,
            x.soft_bin,
            x.errCode,
            x.ft_fail_qty,
            COALESCE(r.recovered_qty, 0) AS recovered_qty,
            i.total_input_qty,
            ROUND(
                100.0 * COALESCE(r.recovered_qty, 0) / NULLIF(i.total_input_qty, 0),
                2
            ) AS recovery_contribution_pct,
            ROUND(
                100.0 * COALESCE(r.recovered_qty, 0) / NULLIF(x.ft_fail_qty, 0),
                2
            ) AS recovery_rate_pct
        FROM ft_fail_by_err x
        LEFT JOIN recovered_by_err r
            ON x.test_date = r.test_date
           AND x.soft_bin = r.soft_bin
           AND x.errCode = r.errCode
        INNER JOIN input_base i
            ON x.test_date = i.test_date
        ORDER BY x.test_date, recovery_contribution_pct DESC, recovered_qty DESC, errCode, soft_bin
    """


# =========================================================
# ROW 6 SQL
# =========================================================
def get_handler_rpr_distribution_sql(
    device_code_value: str,
    station_value: str | None = None,
    selected_schedule: str = "ALL"
) -> str:
    filters = [
        f"COALESCE(TRIM(CAST(h.device_code AS VARCHAR)), '') = '{sql_safe(device_code_value)}'"
    ]

    if station_value is not None:
        filters.append(
            f"COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') = '{sql_safe(station_value)}'"
        )
    else:
        filters.append(
            "COALESCE(TRIM(CAST(h.station AS VARCHAR)), '') NOT IN ('1010','1011','1012')"
        )

    if selected_schedule != "ALL":
        filters.append(f"h.schedule_no = '{sql_safe(selected_schedule)}'")

    where_sql = "WHERE " + " AND ".join(filters)

    unknown_exclSIPon_sql = build_qx_unknown_exclSIPon_sql(
        device_code_value=device_code_value,
        station_value=station_value,
        sb_expr="COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN')",
        err_expr="COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN')"
    )

    excluded_retest_station_sql = build_excluded_retest_station_sql(
        header_station_expr="h.station",
        flow_expr="d.flow"
    )

    return f"""
        WITH joined_data AS (
            SELECT
                h.device_code,
                h.station,
                h.schedule_no,
                CAST(h.end_time AS DATE) AS test_date,
                d.serial_no,
                d.flow,
                COALESCE(NULLIF(TRIM(CAST(d.test_id AS VARCHAR)), ''), 'UNKNOWN') AS test_id,
                COALESCE(NULLIF(TRIM(CAST(d.site AS VARCHAR)), ''), 'UNKNOWN') AS site,
                COALESCE(NULLIF(TRIM(CAST(d.sb AS VARCHAR)), ''), 'UNKNOWN') AS soft_bin,
                COALESCE(NULLIF(TRIM(CAST(d.errCode AS VARCHAR)), ''), 'UNKNOWN') AS errCode,
                d.pf_status,
                d.test_datetime
            FROM {DETAIL_TABLE} d
            INNER JOIN {HEADER_TABLE} h
                ON {get_detail_join_condition("h", "d", station_value)}
            {where_sql}
              AND d.serial_no IS NOT NULL
              AND TRIM(CAST(d.serial_no AS VARCHAR)) <> ''
            {unknown_exclSIPon_sql}
            {excluded_retest_station_sql}
        ),
        max_day AS (
            SELECT MAX(test_date) AS latest_day
            FROM joined_data
        ),
        base AS (
            SELECT *
            FROM joined_data
            WHERE test_date BETWEEN
                (SELECT latest_day - INTERVAL 27 DAY FROM max_day)
                AND
                (SELECT latest_day FROM max_day)
        ),
        ft_first AS (
            SELECT *
            FROM base
            WHERE flow = 'FT'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY test_date, schedule_no, serial_no
                ORDER BY test_datetime ASC
            ) = 1
        ),
        rt_pass AS (
            SELECT
                test_date,
                schedule_no,
                serial_no,
                MAX(CASE WHEN flow IN ('RT1', 'RT2') AND pf_status = 'PASS' THEN 1 ELSE 0 END) AS has_rt_pass
            FROM base
            GROUP BY test_date, schedule_no, serial_no
        ),
        handler_input AS (
            SELECT
                test_date,
                CASE
                    WHEN REGEXP_REPLACE(test_id, '^1000-', '') IN ('NA', '', 'NULL')
                    THEN 'VM Fail'
                    ELSE REGEXP_REPLACE(test_id, '^1000-', '')
                END AS handler,
                site,
                COUNT(DISTINCT serial_no) AS handler_input_qty
            FROM ft_first
            GROUP BY 1, 2, 3
        ),
        grouped AS (
            SELECT
                f.test_date,
                REGEXP_REPLACE(f.test_id, '^1000-', '') AS handler,
                f.site,
                f.soft_bin,
                f.errCode,
                COUNT(DISTINCT f.serial_no) AS retest_pass_fail_qty
            FROM ft_first f
            INNER JOIN rt_pass r
                ON f.test_date = r.test_date
               AND f.schedule_no = r.schedule_no
               AND f.serial_no = r.serial_no
            WHERE f.pf_status = 'FAIL'
              AND r.has_rt_pass = 1
            GROUP BY 1, 2, 3, 4, 5
        )
        SELECT
            g.test_date,
            g.handler,
            g.site,
            g.soft_bin,
            g.errCode,
            g.retest_pass_fail_qty,
            h.handler_input_qty,
            ROUND(
                100.0 * g.retest_pass_fail_qty / NULLIF(h.handler_input_qty, 0),
                2
            ) AS rpr_pct
        FROM grouped g
        INNER JOIN handler_input h
            ON g.test_date = h.test_date
           AND g.handler = h.handler
           AND g.site = h.site
        ORDER BY g.test_date, rpr_pct DESC, retest_pass_fail_qty DESC, handler, site, errCode
    """


# =========================================================
# NEW ROW - MOTHER LOT 4-WEEK BUILDER
# =========================================================
def build_4week_mother_lot_trend_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")

    if out.empty:
        return pd.DataFrame()

    latest_day = out["test_date"].dt.date.max()
    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    prev_week_starts = [
        current_week_start - pd.Timedelta(days=21),
        current_week_start - pd.Timedelta(days=14),
        current_week_start - pd.Timedelta(days=7),
    ]

    def week_label(d):
        dt = pd.Timestamp(d)
        week_num = int(dt.strftime("%U")) + 1
        year_2 = dt.strftime("%y")
        return f"WW{week_num:02d}'{year_2}"

    def aggregate_bucket(sub_df: pd.DataFrame):
        if sub_df.empty:
            return {
                "Mother Lot Count": None,
                "Test-In QTY": None,
                "Final Output": None,
                "1st Yield": None,
                "Final Yield": None,
            }

        mother_lot_count = pd.to_numeric(sub_df["mother_lot_count"], errors="coerce").fillna(0).sum()
        input_qty = pd.to_numeric(sub_df["input_quantity"], errors="coerce").fillna(0).sum()
        first_pass_qty = pd.to_numeric(sub_df["first_pass_qty"], errors="coerce").fillna(0).sum()
        final_pass_qty = pd.to_numeric(sub_df["final_pass_qty"], errors="coerce").fillna(0).sum()

        if input_qty <= 0:
            return {
                "Mother Lot Count": int(mother_lot_count),
                "Test-In QTY": 0,
                "Final Output": 0,
                "1st Yield": None,
                "Final Yield": None,
            }

        return {
            "Mother Lot Count": int(mother_lot_count),
            "Test-In QTY": int(input_qty),
            "Final Output": int(final_pass_qty),
            "1st Yield": round(100.0 * first_pass_qty / input_qty, 2),
            "Final Yield": round(100.0 * final_pass_qty / input_qty, 2),
        }

    rows = []

    for ws in prev_week_starts:
        we = ws + pd.Timedelta(days=6)
        sub = out[(out["test_date"].dt.date >= ws) & (out["test_date"].dt.date <= we)].copy()
        rows.append({
            "x_label": week_label(ws),
            "bucket_type": "week",
            "sort_key": pd.Timestamp(ws),
            **aggregate_bucket(sub)
        })

    current_sub = out[
        (out["test_date"].dt.date >= current_week_start) &
        (out["test_date"].dt.date <= latest_day)
    ].copy()

    rows.append({
        "x_label": week_label(current_week_start),
        "bucket_type": "week_running_total",
        "sort_key": pd.Timestamp(current_week_start),
        **aggregate_bucket(current_sub)
    })

    for i in range(7):
        day = current_week_start + pd.Timedelta(days=i)
        sub = out[out["test_date"].dt.date == day].copy()

        if day > latest_day:
            metrics = {
                "Mother Lot Count": None,
                "Test-In QTY": None,
                "Final Output": None,
                "1st Yield": None,
                "Final Yield": None,
            }
        else:
            metrics = aggregate_bucket(sub)

        rows.append({
            "x_label": f"{day.month}/{day.day:02d}",
            "bucket_type": "day",
            "sort_key": pd.Timestamp(day),
            **metrics
        })

    return pd.DataFrame(rows)

# =========================================================
# 4-WEEK HELPERS
# =========================================================
def build_4week_trend_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")

    if out.empty:
        return pd.DataFrame()

    latest_day = out["test_date"].dt.date.max()
    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    prev_week_starts = [
        current_week_start - pd.Timedelta(days=21),
        current_week_start - pd.Timedelta(days=14),
        current_week_start - pd.Timedelta(days=7),
    ]

    def week_label(d):
        dt = pd.Timestamp(d)
        week_num = int(dt.strftime("%U")) + 1
        year_2 = dt.strftime("%y")
        return f"WW{week_num:02d}'{year_2}"

    def weighted_metrics(sub_df: pd.DataFrame):
        if sub_df.empty:
            return {
                "Test-In QTY": None,
                "Final Output": None,
                "1st Yield": None,
                "RT1 Yield": None,
                "Final Yield": None,
                "Retest rate": None,
            }

        input_qty = pd.to_numeric(sub_df["input_quantity"], errors="coerce").fillna(0).sum()
        first_pass_qty = pd.to_numeric(sub_df["first_pass_qty"], errors="coerce").fillna(0).sum()
        rt1_pass_qty = pd.to_numeric(sub_df["rt1_pass_qty"], errors="coerce").fillna(0).sum()
        final_pass_qty = pd.to_numeric(sub_df["final_pass_qty"], errors="coerce").fillna(0).sum()
        retest_pass_qty = pd.to_numeric(sub_df["retest_pass_qty"], errors="coerce").fillna(0).sum()

        if input_qty <= 0:
            return {
                "Test-In QTY": 0,
                "Final Output": 0,
                "1st Yield": None,
                "RT1 Yield": None,
                "Final Yield": None,
                "Retest rate": None,
            }

        return {
            "Test-In QTY": int(input_qty),
            "Final Output": int(final_pass_qty),
            "1st Yield": round(100.0 * first_pass_qty / input_qty, 2),
            "RT1 Yield": round(100.0 * rt1_pass_qty / input_qty, 2),
            "Final Yield": round(100.0 * final_pass_qty / input_qty, 2),
            "Retest rate": round(100.0 * retest_pass_qty / input_qty, 2),
        }

    rows = []

    for ws in prev_week_starts:
        we = ws + pd.Timedelta(days=6)
        sub = out[(out["test_date"].dt.date >= ws) & (out["test_date"].dt.date <= we)].copy()
        metrics = weighted_metrics(sub)
        rows.append({
            "x_label": week_label(ws),
            "bucket_type": "week",
            "sort_key": pd.Timestamp(ws),
            **metrics
        })

    current_sub = out[
        (out["test_date"].dt.date >= current_week_start) &
        (out["test_date"].dt.date <= latest_day)
    ].copy()

    current_metrics = weighted_metrics(current_sub)
    rows.append({
        "x_label": week_label(current_week_start),
        "bucket_type": "week_running_total",
        "sort_key": pd.Timestamp(current_week_start),
        **current_metrics
    })

    for i in range(7):
        day = current_week_start + pd.Timedelta(days=i)
        sub = out[out["test_date"].dt.date == day].copy()

        if day > latest_day:
            metrics = {
                "Test-In QTY": None,
                "Final Output": None,
                "1st Yield": None,
                "RT1 Yield": None,
                "Final Yield": None,
                "Retest rate": None,
            }
        else:
            metrics = weighted_metrics(sub)

        rows.append({
            "x_label": f"{day.month}/{day.day:02d}",
            "bucket_type": "day",
            "sort_key": pd.Timestamp(day),
            **metrics
        })

    return pd.DataFrame(rows)


def build_4week_fty_errcode_chart_df(
    errcode_df: pd.DataFrame,
    daily_trend_df: pd.DataFrame
) -> pd.DataFrame:
    if errcode_df.empty or daily_trend_df.empty:
        return pd.DataFrame()

    err_df = errcode_df.copy()
    err_df["test_date"] = pd.to_datetime(err_df["test_date"], errors="coerce")
    err_df = err_df.dropna(subset=["test_date"]).sort_values("test_date")

    trend_df = daily_trend_df.copy()
    trend_df["test_date"] = pd.to_datetime(trend_df["test_date"], errors="coerce")
    trend_df = trend_df.dropna(subset=["test_date"]).sort_values("test_date")

    # NEW: fixed Top5 based on latest available day only
    # latest_day = out["test_date"].dt.date.max()
    latest_day = err_df["test_date"].dt.date.max()

    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    # latest_day_df = out[out["test_date"].dt.date == latest_day].copy()
    latest_day_df = err_df[err_df["test_date"].dt.date == latest_day].copy()

    latest_day_df["fail_qty"] = pd.to_numeric(
        latest_day_df["fail_qty"],
        errors="coerce"
    ).fillna(0)

    latest_day_df = latest_day_df[
        (latest_day_df["fail_qty"] > 0) &
        (latest_day_df["soft_bin"].astype(str) != "-")
    ].copy()

    latest_day_df["err_label"] = (
        latest_day_df["soft_bin"].astype(str).str.strip() + "\n" +
        latest_day_df["errCode"].astype(str).str.strip()
    )

    top5_labels = (
        latest_day_df.groupby("err_label", as_index=False)
        .agg(fail_qty=("fail_qty", "sum"))
        .sort_values(["fail_qty", "err_label"], ascending=[False, True])
        .head(5)["err_label"]
        .tolist()
    )

    prev_week_starts = [
        current_week_start - pd.Timedelta(days=21),
        current_week_start - pd.Timedelta(days=14),
        current_week_start - pd.Timedelta(days=7),
    ]

    def week_label(d):
        dt = pd.Timestamp(d)
        week_num = int(dt.strftime("%U")) + 1
        return f"WW{week_num:02d}'{dt.strftime('%y')}"

    def aggregate_bucket(err_sub_df, trend_sub_df):
        total_input = pd.to_numeric(trend_sub_df["input_quantity"], errors="coerce").fillna(0).sum()
        total_output = pd.to_numeric(trend_sub_df["final_pass_qty"], errors="coerce").fillna(0).sum()
        total_first = pd.to_numeric(trend_sub_df["first_pass_qty"], errors="coerce").fillna(0).sum()

        fty = round(100 * total_output / total_input, 2) if total_input > 0 else None
        fpy = round(100 * total_first / total_input, 2) if total_input > 0 else None

        result = {"FTY": fty, "FPY": fpy}

        for i in range(5):
            result[f"top{i+1}_label"] = top5_labels[i] if i < len(top5_labels) else "-"
            result[f"top{i+1}_fail_pct"] = 0

        result["other_fail_pct"] = 0

        if err_sub_df.empty or total_input <= 0:
            return result

        work = err_sub_df.copy()
        work["fail_qty"] = pd.to_numeric(work["fail_qty"], errors="coerce").fillna(0)
        work["err_label"] = (
            work["soft_bin"].astype(str).str.strip() + "\n" +
            work["errCode"].astype(str).str.strip()
        )

        grouped = (
            work.groupby("err_label", as_index=False)
            .agg(fail_qty=("fail_qty", "sum"))
        )

        for i, label in enumerate(top5_labels, start=1):
            qty = grouped.loc[grouped["err_label"] == label, "fail_qty"].sum()
            result[f"top{i}_fail_pct"] = round(100 * qty / total_input, 2)

        other_qty = grouped.loc[
            ~grouped["err_label"].isin(top5_labels),
            "fail_qty"
        ].sum()

        result["other_fail_pct"] = round(100 * other_qty / total_input, 2)

        return result

    rows = []

    for ws in prev_week_starts:
        we = ws + pd.Timedelta(days=6)
        rows.append({
            "x_label": week_label(ws),
            "bucket_type": "week",
            "sort_key": pd.Timestamp(ws),
            **aggregate_bucket(
                err_df[(err_df["test_date"].dt.date >= ws) & (err_df["test_date"].dt.date <= we)],
                trend_df[(trend_df["test_date"].dt.date >= ws) & (trend_df["test_date"].dt.date <= we)]
            )
        })

    rows.append({
        "x_label": week_label(current_week_start),
        "bucket_type": "week_running_total",
        "sort_key": pd.Timestamp(current_week_start),
        **aggregate_bucket(
            err_df[(err_df["test_date"].dt.date >= current_week_start) & (err_df["test_date"].dt.date <= latest_day)],
            trend_df[(trend_df["test_date"].dt.date >= current_week_start) & (trend_df["test_date"].dt.date <= latest_day)]
        )
    })

    for i in range(7):
        day = current_week_start + pd.Timedelta(days=i)
        # rows.append({
        #     "x_label": f"{day.month}/{day.day:02d}",
        #     "bucket_type": "day",
        #     "sort_key": pd.Timestamp(day),
        #     **aggregate_bucket(
        #         err_df[err_df["test_date"].dt.date == day],
        #         trend_df[trend_df["test_date"].dt.date == day]
        #     ) if day <= latest_day else {
        #         "FTY": None, "FPY": None,
        #         **{f"top{i}_label": top5_labels[i-1] if i <= len(top5_labels) else "-" for i in range(1, 6)},
        #         **{f"top{i}_fail_pct": None for i in range(1, 6)},
        #         "other_fail_pct": None,
        #     }
        # })
        if day <= latest_day:
            bucket_result = aggregate_bucket(
                err_df[err_df["test_date"].dt.date == day],
                trend_df[trend_df["test_date"].dt.date == day]
            )
        else:
            bucket_result = {
                "FTY": None,
                "FPY": None,
                **{f"top{i}_label": top5_labels[i-1] if i <= len(top5_labels) else "-" for i in range(1, 6)},
                **{f"top{i}_fail_pct": None for i in range(1, 6)},
                "other_fail_pct": None,
            }

        rows.append({
            "x_label": f"{day.month}/{day.day:02d}",
            "bucket_type": "day",
            "sort_key": pd.Timestamp(day),
            **bucket_result
        })

    return pd.DataFrame(rows)


def build_4week_lrr_display_df(raw_summary_df: pd.DataFrame) -> pd.DataFrame:
    if raw_summary_df.empty:
        return pd.DataFrame()

    df = raw_summary_df.copy()
    df["test_date"] = pd.to_datetime(df["test_date"], errors="coerce")
    df = df.dropna(subset=["test_date"]).sort_values("test_date")

    if df.empty:
        return pd.DataFrame()

    latest_day = df["test_date"].dt.date.max()
    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    prev_week_starts = [
        current_week_start - pd.Timedelta(days=21),
        current_week_start - pd.Timedelta(days=14),
        current_week_start - pd.Timedelta(days=7),
    ]

    def week_label(d):
        dt = pd.Timestamp(d)
        week_num = int(dt.strftime("%U")) + 1
        year_2 = dt.strftime("%y")
        return f"WW{week_num:02d}'{year_2}"

    def aggregate_lrr(sub_df: pd.DataFrame):
        if sub_df.empty:
            return {
                "mother_lot_list": "-",
                "total_lot_count": None,
                "lrr_count": None,
                "lrr_pct": None,
                "lrr_lot_list": "-"
            }

        total_lot_count = pd.to_numeric(sub_df["total_lot_count"], errors="coerce").fillna(0).sum()
        lrr_count = pd.to_numeric(sub_df["lrr_count"], errors="coerce").fillna(0).sum()

        mother_vals = []
        for val in sub_df["mother_lot_list"].fillna("-").tolist():
            for x in str(val).split("\n"):
                x = x.strip()
                if x and x != "-":
                    mother_vals.append(x)

        lrr_vals = []
        for val in sub_df["lrr_lot_list"].fillna("-").tolist():
            for x in str(val).split("\n"):
                x = x.strip()
                if x and x != "-":
                    lrr_vals.append(x)

        mother_unique = sorted(set(mother_vals))
        lrr_unique = sorted(set(lrr_vals))

        return {
            "mother_lot_list": "\n".join(mother_unique) if mother_unique else "-",
            "total_lot_count": int(total_lot_count),
            "lrr_count": int(lrr_count),
            "lrr_pct": round(100.0 * lrr_count / total_lot_count, 2) if total_lot_count > 0 else None,
            "lrr_lot_list": "\n".join(lrr_unique) if lrr_unique else "-"
        }

    rows = []

    for ws in prev_week_starts:
        we = ws + pd.Timedelta(days=6)
        sub = df[(df["test_date"].dt.date >= ws) & (df["test_date"].dt.date <= we)].copy()
        metrics = aggregate_lrr(sub)
        rows.append({
            "x_label": week_label(ws),
            "bucket_type": "week",
            "sort_key": pd.Timestamp(ws),
            **metrics
        })

    current_sub = df[
        (df["test_date"].dt.date >= current_week_start) &
        (df["test_date"].dt.date <= latest_day)
    ].copy()
    metrics = aggregate_lrr(current_sub)
    rows.append({
        "x_label": week_label(current_week_start),
        "bucket_type": "week_running_total",
        "sort_key": pd.Timestamp(current_week_start),
        **metrics
    })

    for i in range(7):
        day = current_week_start + pd.Timedelta(days=i)
        sub = df[df["test_date"].dt.date == day].copy()

        if day > latest_day:
            metrics = {
                "mother_lot_list": "-",
                "total_lot_count": None,
                "lrr_count": None,
                "lrr_pct": None,
                "lrr_lot_list": "-"
            }
        else:
            metrics = aggregate_lrr(sub)

        rows.append({
            "x_label": f"{day.month}/{day.day:02d}",
            "bucket_type": "day",
            "sort_key": pd.Timestamp(day),
            **metrics
        })

    return pd.DataFrame(rows)


def build_4week_rpr_errcode_chart_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")

    if out.empty:
        return pd.DataFrame()

    latest_day = out["test_date"].dt.date.max()
    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    prev_week_starts = [
        current_week_start - pd.Timedelta(days=21),
        current_week_start - pd.Timedelta(days=14),
        current_week_start - pd.Timedelta(days=7),
    ]

    def week_label(d):
        dt = pd.Timestamp(d)
        week_num = int(dt.strftime("%U")) + 1
        year_2 = dt.strftime("%y")
        return f"WW{week_num:02d}'{year_2}"

    def aggregate_bucket(sub_df: pd.DataFrame):
        if sub_df.empty:
            return {
                "RPR": None,
                "top1_label": "-",
                "top1_rpr_pct": None,
                "top2_label": "-",
                "top2_rpr_pct": None,
                "top3_label": "-",
                "top3_rpr_pct": None,
                "top4_label": "-",
                "top4_rpr_pct": None,
                "top5_label": "-",
                "top5_rpr_pct": None,
                "other_rpr_pct": None,
            }

        work = sub_df.copy()
        work["ft_fail_qty"] = pd.to_numeric(work["ft_fail_qty"], errors="coerce").fillna(0)
        work["recovered_qty"] = pd.to_numeric(work["recovered_qty"], errors="coerce").fillna(0)

        total_ft_fail = work["ft_fail_qty"].sum()
        total_recovered = work["recovered_qty"].sum()

        if total_ft_fail <= 0:
            return {
                "RPR": None,
                "top1_label": "-",
                "top1_rpr_pct": None,
                "top2_label": "-",
                "top2_rpr_pct": None,
                "top3_label": "-",
                "top3_rpr_pct": None,
                "top4_label": "-",
                "top4_rpr_pct": None,
                "top5_label": "-",
                "top5_rpr_pct": None,
                "other_rpr_pct": None,
            }

        grouped = (
            work.groupby(["errCode", "soft_bin"], as_index=False)
            .agg(
                ft_fail_qty=("ft_fail_qty", "sum"),
                recovered_qty=("recovered_qty", "sum")
            )
        )

        grouped = grouped[grouped["ft_fail_qty"] > 0].copy()

        if grouped.empty:
            return {
                "RPR": round(100.0 * total_recovered / total_ft_fail, 2),
                "top1_label": "-",
                "top1_rpr_pct": 0,
                "top2_label": "-",
                "top2_rpr_pct": 0,
                "top3_label": "-",
                "top3_rpr_pct": 0,
                "top4_label": "-",
                "top4_rpr_pct": 0,
                "top5_label": "-",
                "top5_rpr_pct": 0,
                "other_rpr_pct": 0,
            }

        grouped["rpr_pct"] = (
            100.0 * grouped["recovered_qty"] / grouped["ft_fail_qty"]
        ).round(2)

        grouped["err_label"] = (
            grouped["errCode"].astype(str).str.strip() + "\n" +
            grouped["soft_bin"].astype(str).str.strip()
        )

        grouped = grouped.sort_values(
            ["rpr_pct", "recovered_qty", "ft_fail_qty", "err_label"],
            ascending=[False, False, False, True]
        ).reset_index(drop=True)

        top_rows = grouped.head(5).copy()
        other = grouped.iloc[5:].copy()

        if not other.empty:
            other_ft_fail = other["ft_fail_qty"].sum()
            other_recovered = other["recovered_qty"].sum()
            other_rpr_pct = round(100.0 * other_recovered / other_ft_fail, 2) if other_ft_fail > 0 else 0
        else:
            other_rpr_pct = 0

        result = {
            "RPR": round(100.0 * total_recovered / total_ft_fail, 2),
            "top1_label": "-",
            "top1_rpr_pct": 0,
            "top2_label": "-",
            "top2_rpr_pct": 0,
            "top3_label": "-",
            "top3_rpr_pct": 0,
            "top4_label": "-",
            "top4_rpr_pct": 0,
            "top5_label": "-",
            "top5_rpr_pct": 0,
            "other_rpr_pct": other_rpr_pct,
        }

        for idx in range(5):
            if idx < len(top_rows):
                result[f"top{idx+1}_label"] = top_rows.iloc[idx]["err_label"]
                result[f"top{idx+1}_rpr_pct"] = top_rows.iloc[idx]["rpr_pct"]

        return result

    rows = []

    for ws in prev_week_starts:
        we = ws + pd.Timedelta(days=6)
        sub = out[(out["test_date"].dt.date >= ws) & (out["test_date"].dt.date <= we)].copy()
        metrics = aggregate_bucket(sub)
        rows.append({
            "x_label": week_label(ws),
            "bucket_type": "week",
            "sort_key": pd.Timestamp(ws),
            **metrics
        })

    current_sub = out[
        (out["test_date"].dt.date >= current_week_start) &
        (out["test_date"].dt.date <= latest_day)
    ].copy()
    metrics = aggregate_bucket(current_sub)
    rows.append({
        "x_label": week_label(current_week_start),
        "bucket_type": "week_running_total",
        "sort_key": pd.Timestamp(current_week_start),
        **metrics
    })

    for i in range(7):
        day = current_week_start + pd.Timedelta(days=i)
        sub = out[out["test_date"].dt.date == day].copy()

        if day > latest_day:
            metrics = {
                "RPR": None,
                "top1_label": "-",
                "top1_rpr_pct": None,
                "top2_label": "-",
                "top2_rpr_pct": None,
                "top3_label": "-",
                "top3_rpr_pct": None,
                "top4_label": "-",
                "top4_rpr_pct": None,
                "top5_label": "-",
                "top5_rpr_pct": None,
                "other_rpr_pct": None,
            }
        else:
            metrics = aggregate_bucket(sub)

        rows.append({
            "x_label": f"{day.month}/{day.day:02d}",
            "bucket_type": "day",
            "sort_key": pd.Timestamp(day),
            **metrics
        })

    return pd.DataFrame(rows)


def build_4week_retest_rate_errcode_chart_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")

    if out.empty:
        return pd.DataFrame()

    latest_day = out["test_date"].dt.date.max()
    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    # fixed Top5 based on latest available day only
    latest_day_df = out[out["test_date"].dt.date == latest_day].copy()
    latest_day_df["recovered_qty"] = pd.to_numeric(latest_day_df["recovered_qty"], errors="coerce").fillna(0)

    latest_day_df["err_label"] = (
        latest_day_df["soft_bin"].astype(str).str.strip() + "\n" +
        latest_day_df["errCode"].astype(str).str.strip()
    )

    top5_labels = (
        latest_day_df[latest_day_df["recovered_qty"] > 0]
        .groupby("err_label", as_index=False)
        .agg(recovered_qty=("recovered_qty", "sum"))
        .sort_values(["recovered_qty", "err_label"], ascending=[False, True])
        .head(5)["err_label"]
        .tolist()
    )

    prev_week_starts = [
        current_week_start - pd.Timedelta(days=21),
        current_week_start - pd.Timedelta(days=14),
        current_week_start - pd.Timedelta(days=7),
    ]

    def week_label(d):
        dt = pd.Timestamp(d)
        week_num = int(dt.strftime("%U")) + 1
        year_2 = dt.strftime("%y")
        return f"WW{week_num:02d}'{year_2}"

    def aggregate_bucket(sub_df: pd.DataFrame):
        result = {
            "Retest Rate": None,
            "top1_label": top5_labels[0] if len(top5_labels) > 0 else "-",
            "top1_rr_pct": None,
            "top2_label": top5_labels[1] if len(top5_labels) > 1 else "-",
            "top2_rr_pct": None,
            "top3_label": top5_labels[2] if len(top5_labels) > 2 else "-",
            "top3_rr_pct": None,
            "top4_label": top5_labels[3] if len(top5_labels) > 3 else "-",
            "top4_rr_pct": None,
            "top5_label": top5_labels[4] if len(top5_labels) > 4 else "-",
            "top5_rr_pct": None,
            "other_rr_pct": None,
        }

        if sub_df.empty:
            return result

        work = sub_df.copy()
        work["recovered_qty"] = pd.to_numeric(work["recovered_qty"], errors="coerce").fillna(0)
        work["total_input_qty"] = pd.to_numeric(work["total_input_qty"], errors="coerce").fillna(0)

        total_input_qty = (
            work.groupby("test_date", as_index=False)["total_input_qty"]
            .max()["total_input_qty"]
            .sum()
        )

        if total_input_qty <= 0:
            return result

        work["err_label"] = (
            work["soft_bin"].astype(str).str.strip() + "\n" +
            work["errCode"].astype(str).str.strip()
        )

        grouped = (
            work.groupby("err_label", as_index=False)
            .agg(recovered_qty=("recovered_qty", "sum"))
        )

        total_recovered = grouped["recovered_qty"].sum()
        result["Retest Rate"] = round(100.0 * total_recovered / total_input_qty, 2)

        for i, label in enumerate(top5_labels, start=1):
            qty = grouped.loc[grouped["err_label"] == label, "recovered_qty"].sum()
            result[f"top{i}_rr_pct"] = round(100.0 * qty / total_input_qty, 2)

        other_qty = grouped.loc[
            ~grouped["err_label"].isin(top5_labels),
            "recovered_qty"
        ].sum()

        result["other_rr_pct"] = round(100.0 * other_qty / total_input_qty, 2)

        return result

    rows = []

    for ws in prev_week_starts:
        we = ws + pd.Timedelta(days=6)
        rows.append({
            "x_label": week_label(ws),
            "bucket_type": "week",
            "sort_key": pd.Timestamp(ws),
            **aggregate_bucket(out[(out["test_date"].dt.date >= ws) & (out["test_date"].dt.date <= we)])
        })

    rows.append({
        "x_label": week_label(current_week_start),
        "bucket_type": "week_running_total",
        "sort_key": pd.Timestamp(current_week_start),
        **aggregate_bucket(out[(out["test_date"].dt.date >= current_week_start) & (out["test_date"].dt.date <= latest_day)])
    })

    for i in range(7):
        day = current_week_start + pd.Timedelta(days=i)

        if day <= latest_day:
            bucket_result = aggregate_bucket(out[out["test_date"].dt.date == day])
        else:
            bucket_result = {
                "Retest Rate": None,
                **{f"top{i}_label": top5_labels[i-1] if i <= len(top5_labels) else "-" for i in range(1, 6)},
                **{f"top{i}_rr_pct": None for i in range(1, 6)},
                "other_rr_pct": None,
            }

        rows.append({
            "x_label": f"{day.month}/{day.day:02d}",
            "bucket_type": "day",
            "sort_key": pd.Timestamp(day),
            **bucket_result
        })

    return pd.DataFrame(rows)

# =========================================================
# PERIOD HELPERS FOR YOY / QOQ / MOM
# =========================================================
def aggregate_yield_metrics(sub_df: pd.DataFrame) -> dict:
    if sub_df is None or sub_df.empty:
        return {
            "Test-In QTY": None,
            "Final Output": None,
            "1st Yield": None,
            "FTY": None,
        }

    input_qty = pd.to_numeric(sub_df["input_quantity"], errors="coerce").fillna(0).sum()
    first_pass_qty = pd.to_numeric(sub_df["first_pass_qty"], errors="coerce").fillna(0).sum()
    final_pass_qty = pd.to_numeric(sub_df["final_pass_qty"], errors="coerce").fillna(0).sum()

    if input_qty <= 0:
        return {
            "Test-In QTY": 0,
            "Final Output": 0,
            "1st Yield": None,
            "FTY": None,
        }

    return {
        "Test-In QTY": int(input_qty),
        "Final Output": int(final_pass_qty),
        "1st Yield": round(100.0 * first_pass_qty / input_qty, 2),
        "FTY": round(100.0 * final_pass_qty / input_qty, 2),
    }

## old format for top level charts YoY/QoQ/MoM
def aggregate_defect_metrics(err_sub_df: pd.DataFrame, trend_sub_df: pd.DataFrame) -> dict:
    if trend_sub_df is None or trend_sub_df.empty:
        total_input = 0
        total_output = 0
        total_first_pass = 0
    else:
        total_input = pd.to_numeric(trend_sub_df["input_quantity"], errors="coerce").fillna(0).sum()
        total_output = pd.to_numeric(trend_sub_df["final_pass_qty"], errors="coerce").fillna(0).sum()
        total_first_pass = pd.to_numeric(trend_sub_df["first_pass_qty"], errors="coerce").fillna(0).sum()

    if total_input <= 0:
        return {
            "FTY": None,
            "FPY": None,
            "top1_label": "-",
            "top1_fail_pct": 0,
            "top2_label": "-",
            "top2_fail_pct": 0,
            "top3_label": "-",
            "top3_fail_pct": 0,
            "top4_label": "-",
            "top4_fail_pct": 0,
            "top5_label": "-",
            "top5_fail_pct": 0,
            "other_fail_pct": 0,
        }

    fty_val = round(100.0 * total_output / total_input, 2)
    fpy_val = round(100.0 * total_first_pass / total_input, 2)

    if err_sub_df is None or err_sub_df.empty:
        return {
            "FTY": fty_val,
            "FPY": fpy_val,
            "top1_label": "-",
            "top1_fail_pct": 0,
            "top2_label": "-",
            "top2_fail_pct": 0,
            "top3_label": "-",
            "top3_fail_pct": 0,
            "top4_label": "-",
            "top4_fail_pct": 0,
            "top5_label": "-",
            "top5_fail_pct": 0,
            "other_fail_pct": 0,
        }

    fail_df = err_sub_df[
        (pd.to_numeric(err_sub_df["fail_qty"], errors="coerce").fillna(0) > 0) &
        (err_sub_df["soft_bin"].astype(str) != "-")
    ].copy()

    if fail_df.empty:
        grouped = pd.DataFrame(columns=["err_label", "fail_qty", "fail_pct"])
    else:
        fail_df["err_label"] = (
            fail_df["soft_bin"].astype(str).str.strip() + "\n" +
            fail_df["errCode"].astype(str).str.strip()
        )

        grouped = (
            fail_df.groupby("err_label", as_index=False)
            .agg(fail_qty=("fail_qty", "sum"))
        )

        grouped["fail_pct"] = (100.0 * grouped["fail_qty"] / total_input).round(2)
        grouped = grouped.sort_values(["fail_qty", "err_label"], ascending=[False, True]).reset_index(drop=True)

    top_rows = grouped.head(5).copy()
    other_fail_qty = grouped.iloc[5:]["fail_qty"].sum() if len(grouped) > 5 else 0
    other_fail_pct = round(100.0 * other_fail_qty / total_input, 2) if total_input > 0 else 0

    result = {
        "FTY": fty_val,
        "FPY": fpy_val,
        "top1_label": "-",
        "top1_fail_pct": 0,
        "top2_label": "-",
        "top2_fail_pct": 0,
        "top3_label": "-",
        "top3_fail_pct": 0,
        "top4_label": "-",
        "top4_fail_pct": 0,
        "top5_label": "-",
        "top5_fail_pct": 0,
        "other_fail_pct": other_fail_pct,
    }

    for idx in range(5):
        if idx < len(top_rows):
            result[f"top{idx+1}_label"] = top_rows.iloc[idx]["err_label"]
            result[f"top{idx+1}_fail_pct"] = top_rows.iloc[idx]["fail_pct"]

    return result

# New format - this breaks the bar chart plots
# def aggregate_defect_metrics(
#     err_sub_df: pd.DataFrame,
#     trend_sub_df: pd.DataFrame,
#     top5_labels: list[str] | None = None   # <-- NEW (optional)
# ) -> dict:

#     # -----------------------------
#     # Base totals
#     # -----------------------------
#     if trend_sub_df is None or trend_sub_df.empty:
#         total_input = 0
#         total_output = 0
#         total_first = 0
#     else:
#         total_input = pd.to_numeric(trend_sub_df["input_quantity"], errors="coerce").fillna(0).sum()
#         total_output = pd.to_numeric(trend_sub_df["final_pass_qty"], errors="coerce").fillna(0).sum()
#         total_first = pd.to_numeric(trend_sub_df["first_pass_qty"], errors="coerce").fillna(0).sum()

#     fty = round(100 * total_output / total_input, 2) if total_input > 0 else None
#     fpy = round(100 * total_first / total_input, 2) if total_input > 0 else None

#     # -----------------------------
#     # Init result
#     # -----------------------------
#     result = {
#         "FTY": fty,
#         "FPY": fpy,
#         **{f"top{i}_label": "-" for i in range(1, 6)},
#         **{f"top{i}_fail_pct": 0 for i in range(1, 6)},
#         "other_fail_pct": 0,
#     }

#     if err_sub_df is None or err_sub_df.empty or total_input <= 0:
#         return result

#     # -----------------------------
#     # Prepare err table
#     # -----------------------------
#     work = err_sub_df.copy()

#     work["fail_qty"] = pd.to_numeric(work["fail_qty"], errors="coerce").fillna(0)

#     work = work[
#         (work["fail_qty"] > 0) &
#         (work["soft_bin"].astype(str) != "-")
#     ].copy()

#     if work.empty:
#         return result

#     work["err_label"] = (
#         work["soft_bin"].astype(str).str.strip() + "\n" +
#         work["errCode"].astype(str).str.strip()
#     )

#     grouped = (
#         work.groupby("err_label", as_index=False)
#         .agg(fail_qty=("fail_qty", "sum"))
#     )

#     # -----------------------------
#     # CASE 1: FIXED TOP5 (NEW MODE)
#     # -----------------------------
#     if top5_labels:
#         for i, label in enumerate(top5_labels, start=1):
#             result[f"top{i}_label"] = label

#             qty = grouped.loc[grouped["err_label"] == label, "fail_qty"].sum()
#             result[f"top{i}_fail_pct"] = round(100 * qty / total_input, 2)

#         other_qty = grouped.loc[
#             ~grouped["err_label"].isin(top5_labels),
#             "fail_qty"
#         ].sum()

#         result["other_fail_pct"] = round(100 * other_qty / total_input, 2)

#         return result

#     # -----------------------------
#     # CASE 2: ORIGINAL MODE (YoY/QoQ/MoM)
#     # -----------------------------
#     grouped["fail_pct"] = (100 * grouped["fail_qty"] / total_input).round(2)

#     grouped = grouped.sort_values(
#         ["fail_qty", "err_label"],
#         ascending=[False, True]
#     ).reset_index(drop=True)

#     top_rows = grouped.head(5)

#     for i in range(5):
#         if i < len(top_rows):
#             result[f"top{i+1}_label"] = top_rows.iloc[i]["err_label"]
#             result[f"top{i+1}_fail_pct"] = top_rows.iloc[i]["fail_pct"]

#     other_qty = grouped.iloc[5:]["fail_qty"].sum() if len(grouped) > 5 else 0
#     result["other_fail_pct"] = round(100 * other_qty / total_input, 2)

#     return result

def get_top5_defect_labels_from_df(err_df: pd.DataFrame, reference_df: pd.DataFrame) -> list[str]:
    if err_df is None or err_df.empty or reference_df is None or reference_df.empty:
        return []

    ref = reference_df.copy()
    ref["fail_qty"] = pd.to_numeric(ref["fail_qty"], errors="coerce").fillna(0)

    ref = ref[
        (ref["fail_qty"] > 0) &
        (ref["soft_bin"].astype(str) != "-")
    ].copy()

    if ref.empty:
        return []

    ref["err_label"] = (
        ref["soft_bin"].astype(str).str.strip() + "\n" +
        ref["errCode"].astype(str).str.strip()
    )

    return (
        ref.groupby("err_label", as_index=False)
        .agg(fail_qty=("fail_qty", "sum"))
        .sort_values(["fail_qty", "err_label"], ascending=[False, True])
        .head(5)["err_label"]
        .tolist()
    )

def sunday_week_of_month(dt: pd.Timestamp) -> int:
    first_day = dt.replace(day=1)
    # Sunday-based week indexing
    offset = (first_day.weekday() + 1) % 7
    return ((dt.day + offset - 1) // 7) + 1


def quarter_start_month(month: int) -> int:
    return ((month - 1) // 3) * 3 + 1


def make_period_row(x_label: str, bucket_type: str, sort_key, metrics: dict) -> dict:
    return {
        "x_label": x_label,
        "bucket_type": bucket_type,
        "sort_key": pd.Timestamp(sort_key),
        **metrics
    }


# =========================================================
# YOY BUILDERS
# =========================================================
def build_yoy_trend_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")
    if out.empty:
        return pd.DataFrame()

    latest_day = out["test_date"].dt.date.max()
    current_year = latest_day.year
    rows = []

    # previous 3 full years
    candidate_years = [current_year - 3, current_year - 2, current_year - 1]

    for yr in candidate_years:
        sub = out[out["test_date"].dt.year == yr].copy()
        if sub.empty:
            continue
        rows.append(make_period_row(
            str(yr),
            "year",
            pd.Timestamp(year=yr, month=1, day=1),
            aggregate_yield_metrics(sub)
        ))

    # current year YTD
    current_sub = out[out["test_date"].dt.year == current_year].copy()
    rows.append(make_period_row(f"{current_year} YTD", "year_running_total", pd.Timestamp(year=current_year, month=1, day=1), aggregate_yield_metrics(current_sub)))

    # Jan-Dec current year
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "AXU", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        sub = out[(out["test_date"].dt.year == current_year) & (out["test_date"].dt.month == m)].copy()
        if m > latest_day.month:
            metrics = {"Test-In QTY": None, "Final Output": None, "1st Yield": None, "FTY": None}
        else:
            metrics = aggregate_yield_metrics(sub)
        rows.append(make_period_row(month_names[m - 1], "month", pd.Timestamp(year=current_year, month=m, day=1), metrics))

    return pd.DataFrame(rows)


def build_yoy_fty_errcode_chart_df(errcode_df: pd.DataFrame, daily_trend_df: pd.DataFrame) -> pd.DataFrame:
    if errcode_df.empty or daily_trend_df.empty:
        return pd.DataFrame()

    err_df = errcode_df.copy()
    err_df["test_date"] = pd.to_datetime(err_df["test_date"], errors="coerce")
    err_df = err_df.dropna(subset=["test_date"]).sort_values("test_date")

    trend_df = daily_trend_df.copy()
    trend_df["test_date"] = pd.to_datetime(trend_df["test_date"], errors="coerce")
    trend_df = trend_df.dropna(subset=["test_date"]).sort_values("test_date")

    latest_day = trend_df["test_date"].dt.date.max()
    current_year = latest_day.year

    current_month = latest_day.month
    current_month_err = err_df[
        (err_df["test_date"].dt.year == current_year) &
        (err_df["test_date"].dt.month == current_month)
    ].copy()

    top5_labels = get_top5_defect_labels_from_df(err_df, current_month_err)
    rows = []

    candidate_years = [current_year - 3, current_year - 2, current_year - 1]

    for yr in candidate_years:
        err_sub = err_df[err_df["test_date"].dt.year == yr].copy()
        trend_sub = trend_df[trend_df["test_date"].dt.year == yr].copy()

        if err_sub.empty and trend_sub.empty:
            continue

        rows.append(make_period_row(
            str(yr),
            "year",
            pd.Timestamp(year=yr, month=1, day=1),
            aggregate_defect_metrics(err_sub, trend_sub)
            # aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)
        ))

    current_err = err_df[err_df["test_date"].dt.year == current_year].copy()
    current_trend = trend_df[trend_df["test_date"].dt.year == current_year].copy()
    rows.append(make_period_row(f"{current_year} YTD", "year_running_total", pd.Timestamp(year=current_year, month=1, day=1), aggregate_defect_metrics(current_err, current_trend)))

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "AXU", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        err_sub = err_df[(err_df["test_date"].dt.year == current_year) & (err_df["test_date"].dt.month == m)].copy()
        trend_sub = trend_df[(trend_df["test_date"].dt.year == current_year) & (trend_df["test_date"].dt.month == m)].copy()

        if m > latest_day.month:
            metrics = {
                "FTY": None, "FPY": None,
                "top1_label": "-", "top1_fail_pct": None,
                "top2_label": "-", "top2_fail_pct": None,
                "top3_label": "-", "top3_fail_pct": None,
                "top4_label": "-", "top4_fail_pct": None,
                "top5_label": "-", "top5_fail_pct": None,
                "other_fail_pct": None,
            }
        else:
            metrics = aggregate_defect_metrics(err_sub, trend_sub)
            # metrics = aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)

        rows.append(make_period_row(month_names[m - 1], "month", pd.Timestamp(year=current_year, month=m, day=1), metrics))

    return pd.DataFrame(rows)


# =========================================================
# QOQ BUILDERS
# =========================================================
def build_qoq_trend_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")

    if out.empty:
        return pd.DataFrame()

    latest_ts = out["test_date"].max()

    current_year = latest_ts.year
    current_quarter = ((latest_ts.month - 1) // 3) + 1

    def quarter_start(year: int, quarter: int) -> pd.Timestamp:
        month = (quarter - 1) * 3 + 1
        return pd.Timestamp(year=year, month=month, day=1)

    def quarter_end(year: int, quarter: int) -> pd.Timestamp:
        start = quarter_start(year, quarter)
        return start + pd.DateOffset(months=3) - pd.Timedelta(days=1)

    def shift_quarter(year: int, quarter: int, offset: int) -> tuple[int, int]:
        q_index = (year * 4 + (quarter - 1)) + offset
        new_year = q_index // 4
        new_quarter = (q_index % 4) + 1
        return new_year, new_quarter

    def q_label(year: int, quarter: int) -> str:
        return f"{year} Q{quarter}"

    rows = []

    # previous 3 full quarters
    prev_quarters = [
        shift_quarter(current_year, current_quarter, -3),
        shift_quarter(current_year, current_quarter, -2),
        shift_quarter(current_year, current_quarter, -1),
    ]

    for yr, qtr in prev_quarters:
        q_start = quarter_start(yr, qtr)
        q_end = quarter_end(yr, qtr)

        sub = out[
            (out["test_date"] >= q_start) &
            (out["test_date"] <= q_end)
        ].copy()

        rows.append(make_period_row(
            q_label(yr, qtr),
            "quarter",
            q_start,
            aggregate_yield_metrics(sub)
        ))

    # current quarter running total
    current_q_start = quarter_start(current_year, current_quarter)
    current_sub = out[
        (out["test_date"] >= current_q_start) &
        (out["test_date"] <= latest_ts)
    ].copy()

    rows.append(make_period_row(
        f"{q_label(current_year, current_quarter)} MTD",
        "quarter_running_total",
        current_q_start,
        aggregate_yield_metrics(current_sub)
    ))

    # months of current quarter
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "AXU", "Sep", "Oct", "Nov", "Dec"]
    start_month = (current_quarter - 1) * 3 + 1

    for m in [start_month, start_month + 1, start_month + 2]:
        sub = out[
            (out["test_date"].dt.year == current_year) &
            (out["test_date"].dt.month == m)
        ].copy()

        if m > latest_ts.month:
            metrics = {
                "Test-In QTY": None,
                "Final Output": None,
                "1st Yield": None,
                "FTY": None,
            }
        else:
            metrics = aggregate_yield_metrics(sub)

        rows.append(make_period_row(
            f"{month_names[m - 1]}'{str(current_year)[2:]}",
            "month",
            pd.Timestamp(year=current_year, month=m, day=1),
            metrics
        ))

    return pd.DataFrame(rows)



def build_qoq_fty_errcode_chart_df(errcode_df: pd.DataFrame, daily_trend_df: pd.DataFrame) -> pd.DataFrame:
    if errcode_df.empty or daily_trend_df.empty:
        return pd.DataFrame()

    err_df = errcode_df.copy()
    err_df["test_date"] = pd.to_datetime(err_df["test_date"], errors="coerce")
    err_df = err_df.dropna(subset=["test_date"]).sort_values("test_date")

    trend_df = daily_trend_df.copy()
    trend_df["test_date"] = pd.to_datetime(trend_df["test_date"], errors="coerce")
    trend_df = trend_df.dropna(subset=["test_date"]).sort_values("test_date")

    if err_df.empty or trend_df.empty:
        return pd.DataFrame()

    latest_ts = trend_df["test_date"].max()

    current_month_err = err_df[
        (err_df["test_date"].dt.year == latest_ts.year) &
        (err_df["test_date"].dt.month == latest_ts.month)
    ].copy()

    top5_labels = get_top5_defect_labels_from_df(err_df, current_month_err)

    current_year = latest_ts.year
    current_quarter = ((latest_ts.month - 1) // 3) + 1

    def quarter_start(year: int, quarter: int) -> pd.Timestamp:
        month = (quarter - 1) * 3 + 1
        return pd.Timestamp(year=year, month=month, day=1)

    def quarter_end(year: int, quarter: int) -> pd.Timestamp:
        start = quarter_start(year, quarter)
        return start + pd.DateOffset(months=3) - pd.Timedelta(days=1)

    def shift_quarter(year: int, quarter: int, offset: int) -> tuple[int, int]:
        q_index = (year * 4 + (quarter - 1)) + offset
        new_year = q_index // 4
        new_quarter = (q_index % 4) + 1
        return new_year, new_quarter

    def q_label(year: int, quarter: int) -> str:
        return f"{year} Q{quarter}"

    rows = []

    prev_quarters = [
        shift_quarter(current_year, current_quarter, -3),
        shift_quarter(current_year, current_quarter, -2),
        shift_quarter(current_year, current_quarter, -1),
    ]

    for yr, qtr in prev_quarters:
        q_start = quarter_start(yr, qtr)
        q_end = quarter_end(yr, qtr)

        err_sub = err_df[
            (err_df["test_date"] >= q_start) &
            (err_df["test_date"] <= q_end)
        ].copy()

        trend_sub = trend_df[
            (trend_df["test_date"] >= q_start) &
            (trend_df["test_date"] <= q_end)
        ].copy()

        rows.append(make_period_row(
            q_label(yr, qtr),
            "quarter",
            q_start,
            aggregate_defect_metrics(err_sub, trend_sub)
            # aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)
        ))

    # current quarter running total
    current_q_start = quarter_start(current_year, current_quarter)

    current_err = err_df[
        (err_df["test_date"] >= current_q_start) &
        (err_df["test_date"] <= latest_ts)
    ].copy()

    current_trend = trend_df[
        (trend_df["test_date"] >= current_q_start) &
        (trend_df["test_date"] <= latest_ts)
    ].copy()

    rows.append(make_period_row(
        f"{q_label(current_year, current_quarter)} MTD",
        "quarter_running_total",
        current_q_start,
        aggregate_defect_metrics(err_sub, trend_sub)
        # aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)
    ))

    # months of current quarter
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "AXU", "Sep", "Oct", "Nov", "Dec"]
    start_month = (current_quarter - 1) * 3 + 1

    for m in [start_month, start_month + 1, start_month + 2]:
        err_sub = err_df[
            (err_df["test_date"].dt.year == current_year) &
            (err_df["test_date"].dt.month == m)
        ].copy()

        trend_sub = trend_df[
            (trend_df["test_date"].dt.year == current_year) &
            (trend_df["test_date"].dt.month == m)
        ].copy()

        if m > latest_ts.month:
            metrics = {
                "FTY": None,
                "FPY": None,
                "top1_label": "-",
                "top1_fail_pct": None,
                "top2_label": "-",
                "top2_fail_pct": None,
                "top3_label": "-",
                "top3_fail_pct": None,
                "top4_label": "-",
                "top4_fail_pct": None,
                "top5_label": "-",
                "top5_fail_pct": None,
                "other_fail_pct": None,
            }
        else:
            metrics = aggregate_defect_metrics(err_sub, trend_sub)
            # metrics = aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)

        rows.append(make_period_row(
            f"{month_names[m - 1]}'{str(current_year)[2:]}",
            "month",
            pd.Timestamp(year=current_year, month=m, day=1),
            metrics
        ))

    return pd.DataFrame(rows)


# =========================================================
# MOM BUILDERS
# =========================================================
def build_mom_trend_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["test_date"] = pd.to_datetime(out["test_date"], errors="coerce")
    out = out.dropna(subset=["test_date"]).sort_values("test_date")
    if out.empty:
        return pd.DataFrame()

    latest_day = out["test_date"].dt.date.max()
    current_month_start = pd.Timestamp(year=latest_day.year, month=latest_day.month, day=1)

    prev_month_starts = [
        current_month_start - pd.DateOffset(months=3),
        current_month_start - pd.DateOffset(months=2),
        current_month_start - pd.DateOffset(months=1),
    ]

    rows = []
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "AXU", "Sep", "Oct", "Nov", "Dec"]

    for ms in prev_month_starts:
        me = ms + pd.offsets.MonthEnd(1)
        sub = out[(out["test_date"] >= ms) & (out["test_date"] <= me)].copy()
        rows.append(make_period_row(
            f"{month_names[ms.month - 1]}'{str(ms.year)[2:]}",
            "month",
            ms,
            aggregate_yield_metrics(sub)
        ))

    current_sub = out[(out["test_date"] >= current_month_start) & (out["test_date"].dt.date <= latest_day)].copy()
    rows.append(make_period_row(
        f"{month_names[latest_day.month - 1]}'{str(latest_day.year)[2:]} MTD",
        "month_running_total",
        current_month_start,
        aggregate_yield_metrics(current_sub)
    ))

    current_month_df = out[
        (out["test_date"].dt.year == latest_day.year) &
        (out["test_date"].dt.month == latest_day.month)
    ].copy()

    max_week = 5
    if not current_month_df.empty:
        current_month_df["week_num"] = current_month_df["test_date"].apply(sunday_week_of_month)
        max_week = max(4, int(current_month_df["week_num"].max()))

    for wk in range(1, max_week + 1):
        sub = current_month_df[current_month_df["week_num"] == wk].copy()
        rows.append(make_period_row(
            f"Wk{wk} {month_names[latest_day.month - 1]}'{str(latest_day.year)[2:]}",
            "week",
            current_month_start + pd.Timedelta(days=wk * 7),
            aggregate_yield_metrics(sub)
        ))

    return pd.DataFrame(rows)


def build_mom_fty_errcode_chart_df(errcode_df: pd.DataFrame, daily_trend_df: pd.DataFrame) -> pd.DataFrame:
    if errcode_df.empty or daily_trend_df.empty:
        return pd.DataFrame()

    err_df = errcode_df.copy()
    err_df["test_date"] = pd.to_datetime(err_df["test_date"], errors="coerce")
    err_df = err_df.dropna(subset=["test_date"]).sort_values("test_date")

    trend_df = daily_trend_df.copy()
    trend_df["test_date"] = pd.to_datetime(trend_df["test_date"], errors="coerce")
    trend_df = trend_df.dropna(subset=["test_date"]).sort_values("test_date")

    latest_day = trend_df["test_date"].dt.date.max()
    current_month_start = pd.Timestamp(year=latest_day.year, month=latest_day.month, day=1)

    current_week_start = latest_day - pd.Timedelta(days=(latest_day.weekday() + 1) % 7)
    current_week_start = current_week_start.date() if hasattr(current_week_start, "date") else current_week_start

    current_week_err = err_df[
        (err_df["test_date"].dt.date >= current_week_start) &
        (err_df["test_date"].dt.date <= latest_day)
    ].copy()

    top5_labels = get_top5_defect_labels_from_df(err_df, current_week_err)
    prev_month_starts = [
        current_month_start - pd.DateOffset(months=3),
        current_month_start - pd.DateOffset(months=2),
        current_month_start - pd.DateOffset(months=1),
    ]

    rows = []
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "AXU", "Sep", "Oct", "Nov", "Dec"]

    for ms in prev_month_starts:
        me = ms + pd.offsets.MonthEnd(1)
        err_sub = err_df[(err_df["test_date"] >= ms) & (err_df["test_date"] <= me)].copy()
        trend_sub = trend_df[(trend_df["test_date"] >= ms) & (trend_df["test_date"] <= me)].copy()
        rows.append(make_period_row(
            f"{month_names[ms.month - 1]}'{str(ms.year)[2:]}",
            "month",
            ms,
            aggregate_defect_metrics(err_sub, trend_sub)
            # aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)
        ))

    current_err = err_df[(err_df["test_date"] >= current_month_start) & (err_df["test_date"].dt.date <= latest_day)].copy()
    current_trend = trend_df[(trend_df["test_date"] >= current_month_start) & (trend_df["test_date"].dt.date <= latest_day)].copy()
    rows.append(make_period_row(
        f"{month_names[latest_day.month - 1]}'{str(latest_day.year)[2:]} MTD",
        "month_running_total",
        current_month_start,
        aggregate_defect_metrics(current_err, current_trend)
    ))

    current_month_err = err_df[
        (err_df["test_date"].dt.year == latest_day.year) &
        (err_df["test_date"].dt.month == latest_day.month)
    ].copy()

    current_month_trend = trend_df[
        (trend_df["test_date"].dt.year == latest_day.year) &
        (trend_df["test_date"].dt.month == latest_day.month)
    ].copy()

    if not current_month_err.empty:
        current_month_err["week_num"] = current_month_err["test_date"].apply(sunday_week_of_month)
    if not current_month_trend.empty:
        current_month_trend["week_num"] = current_month_trend["test_date"].apply(sunday_week_of_month)

    max_week = 5
    if not current_month_trend.empty:
        max_week = max(4, int(current_month_trend["week_num"].max()))
    elif not current_month_err.empty:
        max_week = max(4, int(current_month_err["week_num"].max()))

    for wk in range(1, max_week + 1):
        err_sub = current_month_err[current_month_err["week_num"] == wk].copy() if "week_num" in current_month_err.columns else pd.DataFrame()
        trend_sub = current_month_trend[current_month_trend["week_num"] == wk].copy() if "week_num" in current_month_trend.columns else pd.DataFrame()

        rows.append(make_period_row(
            f"Wk{wk} {month_names[latest_day.month - 1]}'{str(latest_day.year)[2:]}",
            "week",
            current_month_start + pd.Timedelta(days=wk * 7),
            aggregate_defect_metrics(err_sub, trend_sub)
            # aggregate_defect_metrics(err_sub, trend_sub, top5_labels=top5_labels)

        ))

    return pd.DataFrame(rows)

# =========================================================
# ROW 6 HELPERS
# =========================================================
def get_rpr_stack_palette():
    return {
        "top1": "#F79646",   # orange
        "top2": "#8064A2",   # purple
        "top3": "#C0504D",   # dark red
        "top4": "#4BACC6",   # teal
        "top5": "#7F7F7F",   # gray
        "other": "#D9D9D9",  # light gray
    }

def build_group_top5_rpr_df(df: pd.DataFrame, group_cols: list[str], top_n_groups: int | None = None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["rpr_pct"] = pd.to_numeric(work["rpr_pct"], errors="coerce").fillna(0)
    work["retest_pass_fail_qty"] = pd.to_numeric(work["retest_pass_fail_qty"], errors="coerce").fillna(0)
    work["handler_input_qty"] = pd.to_numeric(work["handler_input_qty"], errors="coerce").fillna(0)
    work["test_date"] = pd.to_datetime(work["test_date"], errors="coerce")

    if len(group_cols) == 1:
        work["group_name"] = work[group_cols[0]].astype(str)
    else:
        work["group_name"] = work[group_cols].astype(str).agg(" | ".join, axis=1)

    work["err_label"] = work["errCode"].astype(str).str.strip()

    # unique denominator per group + date (+ site if group includes site naturally already)
    group_input_df = (
        work.groupby(group_cols + ["test_date"], as_index=False)["handler_input_qty"]
        .max()
    )
    if len(group_cols) == 1:
        group_input_df["group_name"] = group_input_df[group_cols[0]].astype(str)
    else:
        group_input_df["group_name"] = group_input_df[group_cols].astype(str).agg(" | ".join, axis=1)

    group_total_input = (
        group_input_df.groupby("group_name", as_index=False)["handler_input_qty"]
        .sum()
        .rename(columns={"handler_input_qty": "group_total_input_qty"})
    )

    grouped = (
        work.groupby(["group_name", "err_label"], as_index=False)
        .agg(recovered_qty=("retest_pass_fail_qty", "sum"))
    )

    grouped = grouped.merge(group_total_input, on="group_name", how="left")
    grouped["rpr_pct"] = (
        100.0 * grouped["recovered_qty"] / grouped["group_total_input_qty"].replace(0, pd.NA)
    ).fillna(0).round(2)

    group_rank = (
        grouped.groupby("group_name", as_index=False)
        .agg(
            total_rpr_pct=("rpr_pct", "sum"),
            total_recovered_qty=("recovered_qty", "sum")
        )
        .merge(group_total_input, on="group_name", how="left")
        .sort_values(["total_rpr_pct", "group_name"], ascending=[False, True])
    )

    if top_n_groups is not None:
        keep_groups = group_rank.head(top_n_groups)["group_name"].tolist()
        grouped = grouped[grouped["group_name"].isin(keep_groups)].copy()
        group_rank = group_rank[group_rank["group_name"].isin(keep_groups)].copy()

    rows = []
    for group_name in group_rank["group_name"].tolist():
        sub = grouped[grouped["group_name"] == group_name].copy()
        sub = sub.sort_values(["rpr_pct", "recovered_qty", "err_label"], ascending=[False, False, True]).reset_index(drop=True)

        total_input_qty = float(sub["group_total_input_qty"].iloc[0]) if not sub.empty else 0.0
        total_recovered_qty = float(sub["recovered_qty"].sum()) if not sub.empty else 0.0

        top = sub.head(5).copy()
        other = sub.iloc[5:].copy()

        row = {"group_name": group_name}
        for i in range(5):
            if i < len(top):
                row[f"top{i+1}_errCode"] = top.iloc[i]["err_label"]
                row[f"top{i+1}_rpr_pct"] = top.iloc[i]["rpr_pct"]
            else:
                row[f"top{i+1}_errCode"] = "-"
                row[f"top{i+1}_rpr_pct"] = 0.0

        row["other_errCodes"] = "Other errCodes"
        row["other_rpr_pct"] = float(other["rpr_pct"].sum()) if not other.empty else 0.0
        row["total_rpr_pct"] = round(100.0 * total_recovered_qty / total_input_qty, 2) if total_input_qty > 0 else 0.0
        row["group_total_input_qty"] = total_input_qty
        row["group_total_recovered_qty"] = total_recovered_qty

        rows.append(row)

    return pd.DataFrame(rows)

def build_handler_site_7day_rpr_timeseries_df(
    df: pd.DataFrame,
    handler_value: str,
    site_value: str
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["test_date"] = pd.to_datetime(work["test_date"], errors="coerce")
    work = work.dropna(subset=["test_date"])

    work = work[
        (work["handler"].astype(str) == str(handler_value)) &
        (work["site"].astype(str) == str(site_value))
    ].copy()

    if work.empty:
        return pd.DataFrame()

    # latest available day = previous day in your dashboard logic
    end_day = work["test_date"].dt.normalize().max()
    start_day = end_day - pd.Timedelta(days=6)

    work = work[
        (work["test_date"].dt.normalize() >= start_day) &
        (work["test_date"].dt.normalize() <= end_day)
    ].copy()

    if work.empty:
        return pd.DataFrame()

    work["retest_pass_fail_qty"] = pd.to_numeric(
        work["retest_pass_fail_qty"], errors="coerce"
    ).fillna(0)

    work["handler_input_qty"] = pd.to_numeric(
        work["handler_input_qty"], errors="coerce"
    ).fillna(0)

    work["err_label"] = work["errCode"].astype(str).str.strip()

    # -----------------------------------------------------
    # FIXED TOP ERR CODES FROM LATEST/PREVIOUS DAY ONLY
    # -----------------------------------------------------
    latest_day_df = work[work["test_date"].dt.normalize() == end_day].copy()

    top_labels = (
        latest_day_df.groupby("err_label", as_index=False)
        .agg(recovered_qty=("retest_pass_fail_qty", "sum"))
        .query("recovered_qty > 0")
        .sort_values(["recovered_qty", "err_label"], ascending=[False, True])
        .head(5)["err_label"]
        .tolist()
    )

    rows = []

    for day in pd.date_range(start_day, end_day, freq="D"):
        day_sub = work[work["test_date"].dt.normalize() == day].copy()

        base_row = {
            "x_label": f"{day.month}/{day.day:02d}",
            "sort_key": day,
            "Overall_RPR": None,
            "other_rpr_pct": None,
        }

        for i in range(1, 6):
            base_row[f"top{i}_label"] = top_labels[i - 1] if i <= len(top_labels) else "-"
            base_row[f"top{i}_rpr_pct"] = None

        if day_sub.empty:
            rows.append(base_row)
            continue

        total_input_qty = day_sub["handler_input_qty"].max()
        total_recovered_qty = day_sub["retest_pass_fail_qty"].sum()

        if total_input_qty <= 0:
            rows.append(base_row)
            continue

        grouped = (
            day_sub.groupby("err_label", as_index=False)
            .agg(recovered_qty=("retest_pass_fail_qty", "sum"))
        )

        base_row["Overall_RPR"] = round(
            100.0 * total_recovered_qty / total_input_qty,
            2
        )

        for i, label in enumerate(top_labels, start=1):
            qty = grouped.loc[grouped["err_label"] == label, "recovered_qty"].sum()
            base_row[f"top{i}_rpr_pct"] = round(
                100.0 * qty / total_input_qty,
                2
            )

        other_qty = grouped.loc[
            ~grouped["err_label"].isin(top_labels),
            "recovered_qty"
        ].sum()

        base_row["other_rpr_pct"] = round(
            100.0 * other_qty / total_input_qty,
            2
        )

        rows.append(base_row)

    return pd.DataFrame(rows)

def render_handler_site_7day_timeseries_charts(
    source_df: pd.DataFrame,
    top10_df: pd.DataFrame,
    section_label: str,
    scope_key: str
):
    if source_df is None or source_df.empty or top10_df is None or top10_df.empty:
        return []

    figs = []  

    st.subheader("Top 5 Handler-Site 7-Day RPR Time Series")

    palette = get_rpr_stack_palette()

    top10_ordered = top10_df.sort_values(
        ["total_rpr_pct", "group_name"],
        ascending=[False, True]
    ).head(5).reset_index(drop=True)

    color_map = {
        "top1_rpr_pct": palette["top1"],
        "top2_rpr_pct": palette["top2"],
        "top3_rpr_pct": palette["top3"],
        "top4_rpr_pct": palette["top4"],
        "top5_rpr_pct": palette["top5"],
        "other_rpr_pct": palette["other"],
    }

    label_map = {
        "top1_rpr_pct": "Top1 errCode RPR",
        "top2_rpr_pct": "Top2 errCode RPR",
        "top3_rpr_pct": "Top3 errCode RPR",
        "top4_rpr_pct": "Top4 errCode RPR",
        "top5_rpr_pct": "Top5 errCode RPR",
        "other_rpr_pct": "Other errCodes RPR",
    }

    hover_label_cols = {
        "top1_rpr_pct": "top1_label",
        "top2_rpr_pct": "top2_label",
        "top3_rpr_pct": "top3_label",
        "top4_rpr_pct": "top4_label",
        "top5_rpr_pct": "top5_label",
    }

    for idx, row in top10_ordered.iterrows():
        group_name = str(row["group_name"])

        if " | " in group_name:
            handler_value, site_value = group_name.split(" | ", 1)
        else:
            continue

        label_name = f"{handler_value}-{site_value}"

        plot_df = build_handler_site_7day_rpr_timeseries_df(
            source_df,
            handler_value=handler_value,
            site_value=site_value
        )

        if plot_df.empty:
            continue

        plot_df = plot_df.copy()

        # Convert existing x_label like 04/18 back to full date SIPng current year
        current_year = pd.Timestamp.today().year
        plot_df["plot_date"] = pd.to_datetime(
            str(current_year) + "/" + plot_df["x_label"].astype(str),
            format="%Y/%m/%d",
            errors="coerce"
        )

        max_day = plot_df["plot_date"].max()
        min_day = max_day - pd.Timedelta(days=6)

        full_days = pd.DataFrame({
            "plot_date": pd.date_range(min_day, max_day, freq="D")
        })

        plot_df = full_days.merge(plot_df, on="plot_date", how="left")
        plot_df["x_label"] = plot_df["plot_date"].dt.strftime("%m/%d")

        for c in [
            "top1_rpr_pct", "top2_rpr_pct", "top3_rpr_pct",
            "top4_rpr_pct", "top5_rpr_pct", "other_rpr_pct"
        ]:
            plot_df[c] = pd.to_numeric(plot_df[c], errors="coerce").fillna(0)

        plot_df["Overall_RPR"] = pd.to_numeric(plot_df["Overall_RPR"], errors="coerce")

        x_order = plot_df["x_label"].tolist()

        stack_cols = [
            "top1_rpr_pct", "top2_rpr_pct", "top3_rpr_pct",
            "top4_rpr_pct", "top5_rpr_pct", "other_rpr_pct"
        ]

        stack_max = (
            plot_df[stack_cols]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0)
            .sum(axis=1)
            .max()
        )

        if pd.isna(stack_max):
            stack_max = 2

        auto_y_max = max(2, round(float(stack_max) * 1.25, 2))
        auto_y_max = min(auto_y_max, 100)

        fig = go.Figure()

        for rpr_col in ["top1_rpr_pct", "top2_rpr_pct", "top3_rpr_pct", "top4_rpr_pct", "top5_rpr_pct"]:
            label_col = hover_label_cols[rpr_col]

            # Skip unused Top slots, e.g. if latest day only has Top1-Top3
            valid_labels = plot_df[label_col].dropna().astype(str).str.strip()
            valid_labels = valid_labels[~valid_labels.isin(["", "-", "nan", "None"])]

            if valid_labels.empty:
                continue

            fig.add_trace(
                go.Bar(
                    x=plot_df["x_label"],
                    y=plot_df[rpr_col],
                    name=label_map[rpr_col],
                    marker_color=color_map[rpr_col],
                    opacity=0.85,
                    customdata=plot_df[[hover_label_cols[rpr_col]]].values,
                    hovertemplate=(
                        "Date=%{x}<br>"
                        "errCode=%{customdata[0]}<br>"
                        "RPR=%{y:.2f}%<extra></extra>"
                    )
                )
            )

        fig.add_trace(
            go.Bar(
                x=plot_df["x_label"],
                y=plot_df["other_rpr_pct"],
                name=label_map["other_rpr_pct"],
                marker_color=color_map["other_rpr_pct"],
                opacity=0.85,
                hovertemplate="Date=%{x}<br>Other errCodes RPR=%{y:.2f}%<extra></extra>"
            )
        )

        fig.add_trace(
            go.Scatter(
                x=plot_df["x_label"],
                y=plot_df["Overall_RPR"],
                mode="lines+markers+text",
                name="Overall RPR",
                line=dict(color="red", width=3),
                marker=dict(color="red", size=8),
                text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["Overall_RPR"]],
                textposition="top center",
                textfont=dict(color="black", size=11),
                connectgaps=True,
                hovertemplate="Date=%{x}<br>Overall RPR=%{y:.2f}%<extra></extra>"
            )
        )

        fig.update_layout(
            # title=f"Top {idx + 1}: {label_name} vs Top 10 High RPR errCode",
            title=f"Top {idx + 1}: {label_name} vs Top 5 High RPR errCode",
            height=650,
            barmode="stack",
            hovermode="x unified",
            font=dict(size=12, color="black"),
            dragmode="zoom",
            xaxis=dict(
                title="Date",
                type="category",
                categoryorder="array",
                categoryarray=x_order,
                tickangle=-45,
                tickfont=dict(color="black"),
                title_font=dict(color="black")
            ),
            yaxis=dict(
                title="RPR %",
                range=[0, auto_y_max],
                tickfont=dict(color="black"),
                title_font=dict(color="black")
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.03,
                xanchor="left",
                x=0
            ),
            margin=dict(l=60, r=60, t=100, b=100)
        )

        safe_handler_site = (
            label_name
            .replace(" ", "_")
            .replace("|", "_")
            .replace("-", "_")
            .replace("/", "_")
            .replace("\\", "_")
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
            key=f"handler_site_7day_ts_{scope_key}_{idx + 1}_{safe_handler_site}"
        )

        figs.append(fig)

    return figs

def render_yoy_scope_section(device_code: str, station_value: str | None, section_label: str):
    daily_trend_df = run_query(get_station_period_trend_sql(device_code, station_value))
    daily_summary_errcode_df = run_query(get_daily_summary_errcode_period_sql(device_code, station_value))

    yoy_trend_df = build_yoy_trend_display_df(daily_trend_df)
    yoy_defect_df = build_yoy_fty_errcode_chart_df(daily_summary_errcode_df, daily_trend_df)

    full_daily_df = get_kpi_target_source_df(device_code, station_value)
    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_labels_html = get_kpi_target_labels_html(full_daily_df, monthly_lot_df)

    yield_fig = render_period_yield_trend(
        yoy_trend_df,
        section_label,
        "YoY",
        device_code=device_code,
        station_value=station_value
    )

    st.divider()
    defect_fig, defect_table_df = render_period_fty_errcode_chart(
        yoy_defect_df,
        section_label,
        "YoY"
    )

    return {
        "section_label": section_label,
        "yield_fig": yield_fig,
        "defect_fig": defect_fig,
        "defect_table_df": defect_table_df,
        "target_labels_html": target_labels_html,
    }


def render_qoq_scope_section(device_code: str, station_value: str | None, section_label: str):
    daily_trend_df = run_query(get_station_period_trend_sql(device_code, station_value))
    daily_summary_errcode_df = run_query(get_daily_summary_errcode_period_sql(device_code, station_value))

    qoq_trend_df = build_qoq_trend_display_df(daily_trend_df)
    qoq_defect_df = build_qoq_fty_errcode_chart_df(daily_summary_errcode_df, daily_trend_df)

    full_daily_df = get_kpi_target_source_df(device_code, station_value)
    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_labels_html = get_kpi_target_labels_html(full_daily_df, monthly_lot_df)

    yield_fig = render_period_yield_trend(
        qoq_trend_df,
        section_label,
        "QoQ",
        device_code=device_code,
        station_value=station_value
    )

    st.divider()
    defect_fig, defect_table_df = render_period_fty_errcode_chart(
        qoq_defect_df,
        section_label,
        "QoQ"
    )

    return {
        "section_label": section_label,
        "yield_fig": yield_fig,
        "defect_fig": defect_fig,
        "defect_table_df": defect_table_df,
        "target_labels_html": target_labels_html,
    }

def render_mom_scope_section(device_code: str, station_value: str | None, section_label: str):
    daily_trend_df = run_query(get_station_period_trend_sql(device_code, station_value))
    daily_summary_errcode_df = run_query(get_daily_summary_errcode_period_sql(device_code, station_value))

    mom_trend_df = build_mom_trend_display_df(daily_trend_df)
    mom_defect_df = build_mom_fty_errcode_chart_df(daily_summary_errcode_df, daily_trend_df)

    full_daily_df = get_kpi_target_source_df(device_code, station_value)
    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_labels_html = get_kpi_target_labels_html(full_daily_df, monthly_lot_df)

    yield_fig = render_period_yield_trend(
        mom_trend_df,
        section_label,
        "MoM",
        device_code=device_code,
        station_value=station_value
    )

    st.divider()
    defect_fig, defect_table_df = render_period_fty_errcode_chart(
        mom_defect_df,
        section_label,
        "MoM"
    )

    return {
        "section_label": section_label,
        "yield_fig": yield_fig,
        "defect_fig": defect_fig,
        "defect_table_df": defect_table_df,
        "target_labels_html": target_labels_html,
    }

# =========================================================
# NEW ROW - MOTHER LOT YIELD TREND RENDER
# x-axis = mother lot
# =========================================================
def render_mother_lot_yield_trend(
    df: pd.DataFrame,
    section_label: str,
    scope_key: str,
    full_daily_df: pd.DataFrame,
    device_code: str,
    station_value: str | None
):
    st.subheader("Mother Lot Yield Trend")

    if df.empty:
        st.info("No mother lot yield data available.")
        return None, pd.DataFrame(), "No data"

    filtered_df = add_date_range_slider(
        df,
        key_prefix=f"mother_lot_yield_4w_{scope_key}",
        label="Filter date range for Mother Lot Yield Trend",
        default_last_n_days_from_data_max=28
    )

    if filtered_df.empty:
        st.info("No data in selected date range.")
        return None, pd.DataFrame(), "No data"

    filtered_df = filtered_df.copy()
    filtered_df["test_date"] = pd.to_datetime(filtered_df["test_date"], errors="coerce")

    min_date = filtered_df["test_date"].min()
    max_date = filtered_df["test_date"].max()
    date_scope_label = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"

    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)

    selected_fty_target = float(target_info.get("current_month_fty_target_lcl", 98.00))
    selected_fpy_target = float(target_info.get("current_month_fpy_target_lcl", 95.00))

    agg_df = (
        filtered_df.groupby("mother_lot", as_index=False)
        .agg(
            input_quantity=("input_quantity", "sum"),
            first_pass_qty=("first_pass_qty", "sum"),
            final_pass_qty=("final_pass_qty", "sum"),
            retest_pass_qty=("retest_pass_qty", "sum")
        )
    )

    if agg_df.empty:
        st.info("No mother lot data available after filtering.")
        return None, pd.DataFrame(), date_scope_label

    agg_df["first_pass_yield_pct"] = (
        100.0 * agg_df["first_pass_qty"] / agg_df["input_quantity"].replace(0, pd.NA)
    ).fillna(0).round(2)

    agg_df["final_yield_pct"] = (
        100.0 * agg_df["final_pass_qty"] / agg_df["input_quantity"].replace(0, pd.NA)
    ).fillna(0).round(2)

    agg_df = agg_df.sort_values(
        ["final_pass_qty", "mother_lot"],
        ascending=[False, True]
    ).reset_index(drop=True)

    x_order = agg_df["mother_lot"].tolist()

    yield_df = agg_df[["first_pass_yield_pct", "final_yield_pct"]].apply(pd.to_numeric, errors="coerce")
    target_series = pd.Series([selected_fty_target, selected_fpy_target], dtype="float64")

    max_yield = pd.concat([
        yield_df.stack().dropna(),
        target_series.dropna()
    ]).max()

    min_yield = pd.concat([
        yield_df.stack().dropna(),
        target_series.dropna()
    ]).min()

    auto_yield_min = max(0.0, round(selected_fpy_target - 3.0, 2))
    auto_yield_max = 101.0

    max_qty = pd.to_numeric(
        pd.concat([agg_df["input_quantity"], agg_df["final_pass_qty"]]),
        errors="coerce"
    ).fillna(0).max()

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=agg_df["mother_lot"],
            y=agg_df["input_quantity"],
            name="Test-In QTY",
            yaxis="y",
            marker_color=MEDIUM_BLUE,
            opacity=0.50,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in agg_df["input_quantity"]],
            textposition="outside",
            textfont=dict(color="black", size=11),
            hovertemplate="Mother Lot=%{x}<br>Test-In QTY=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Bar(
            x=agg_df["mother_lot"],
            y=agg_df["final_pass_qty"],
            name="Final Output",
            yaxis="y",
            marker_color=MEDIUM_LIGHT_GREEN,
            opacity=0.45,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in agg_df["final_pass_qty"]],
            textposition="outside",
            textfont=dict(color="black", size=11),
            hovertemplate="Mother Lot=%{x}<br>Final Output=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=agg_df["mother_lot"],
            y=agg_df["first_pass_yield_pct"],
            mode="lines+markers+text",
            name="1st Yield",
            yaxis="y2",
            line=dict(color="#4F81BD", width=3),
            marker=dict(color="#4F81BD", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in agg_df["first_pass_yield_pct"]],
            textposition="top center",
            textfont=dict(color="black", size=10),
            hovertemplate="Mother Lot=%{x}<br>1st Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=agg_df["mother_lot"],
            y=agg_df["final_yield_pct"],
            mode="lines+markers+text",
            name="Final Yield",
            yaxis="y2",
            line=dict(color="#92D050", width=3),
            marker=dict(color="#92D050", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in agg_df["final_yield_pct"]],
            textposition="top center",
            textfont=dict(color="black", size=10),
            hovertemplate="Mother Lot=%{x}<br>Final Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_hline(
        y=selected_fty_target,
        yref="y2",
        line_dash="dash",
        line_color="red",
        annotation_text=f"FTY Target (3-sig LCL): {selected_fty_target:.2f}%",
        annotation_font_color="red"
    )

    fig.add_hline(
        y=selected_fpy_target,
        yref="y2",
        line_dash="dash",
        line_color="black",
        annotation_text=f"FPY Target (3-sig LCL): {selected_fpy_target:.2f}%",
        annotation_font_color="black"
    )

    fig.update_layout(
        title=f"{section_label} — Mother Lot Yield Trend<br><sup>Date Range: {date_scope_label}</sup>",
        height=780,
        barmode="group",
        hovermode="x unified",
        dragmode="zoom",
        xaxis=dict(
            title="Mother Lot",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Quantity",
            side="left",
            range=[0, max(10, round(float(max_qty) * 1.20, 0))]
        ),
        yaxis2=dict(
            title="Yield %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[auto_yield_min, auto_yield_max]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="left",
            x=0
        ),
        margin=dict(l=60, r=80, t=100, b=140)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = agg_df[[
        "mother_lot",
        "input_quantity",
        "final_pass_qty",
        "first_pass_yield_pct",
        "final_yield_pct"
    ]].copy()

    export_df = export_df.rename(columns={
        "mother_lot": "Mother Lot",
        "input_quantity": "Test-In QTY",
        "final_pass_qty": "Final Output",
        "first_pass_yield_pct": "1st Yield",
        "final_yield_pct": "Final Yield"
    })

    # st.dataframe(export_df, use_container_width=True, height=320)

    return fig, export_df, date_scope_label

# =========================================================
# NEW ROW - PER LOT (SCHEDULE NO) YIELD TREND
# =========================================================
def render_schedule_no_yield_trend(
    df: pd.DataFrame,
    section_label: str,
    scope_key: str,
    full_daily_df: pd.DataFrame,
    device_code: str,
    station_value: str | None
):
    st.subheader("Per Lot Yield Trend")

    if df.empty:
        st.info("No schedule no yield data available.")
        return None, pd.DataFrame(), "No data"

    filtered_df = add_date_range_slider(
        df,
        key_prefix=f"schedule_no_yield_prevday_v2_{scope_key}",
        label="Filter date range for Per Lot Yield Trend",
        default_days_ending_yesterday=1
    )

    if filtered_df.empty:
        st.info("No data in selected date range.")
        return None, pd.DataFrame(), "No data"

    filtered_df = filtered_df.copy()
    filtered_df["test_date"] = pd.to_datetime(filtered_df["test_date"], errors="coerce")

    min_date = filtered_df["test_date"].min()
    max_date = filtered_df["test_date"].max()
    date_scope_label = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"

    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)

    selected_fty_target = float(target_info.get("current_month_fty_target_lcl", 98.00))
    selected_fpy_target = float(target_info.get("current_month_fpy_target_lcl", 95.00))

    agg_df = (
        filtered_df.groupby("schedule_no", as_index=False)
        .agg(
            input_quantity=("input_quantity", "sum"),
            first_pass_qty=("first_pass_qty", "sum"),
            final_pass_qty=("final_pass_qty", "sum"),
            retest_pass_qty=("retest_pass_qty", "sum")
        )
    )

    if agg_df.empty:
        st.info("No schedule no data available after filtering.")
        return None, pd.DataFrame(), date_scope_label

    agg_df["first_pass_yield_pct"] = (
        100.0 * agg_df["first_pass_qty"] / agg_df["input_quantity"].replace(0, pd.NA)
    ).fillna(0).round(2)

    agg_df["final_yield_pct"] = (
        100.0 * agg_df["final_pass_qty"] / agg_df["input_quantity"].replace(0, pd.NA)
    ).fillna(0).round(2)

    agg_df = agg_df.sort_values(
        ["final_pass_qty", "schedule_no"],
        ascending=[False, True]
    ).reset_index(drop=True)

    x_order = agg_df["schedule_no"].tolist()

    yield_df = agg_df[["first_pass_yield_pct", "final_yield_pct"]].apply(pd.to_numeric, errors="coerce")
    # max_yield = yield_df.max().max()
    # min_yield = yield_df.min().min()

    target_series = pd.Series([selected_fty_target, selected_fpy_target], dtype="float64")

    max_yield = pd.concat([
        yield_df.stack().dropna(),
        target_series.dropna()
    ]).max()

    min_yield = pd.concat([
        yield_df.stack().dropna(),
        target_series.dropna()
    ]).min()

    auto_yield_min = max(0.0, round(selected_fpy_target - 3.0, 2))
    auto_yield_max = 101.0

    max_qty = pd.to_numeric(
        pd.concat([agg_df["input_quantity"], agg_df["final_pass_qty"]]),
        errors="coerce"
    ).fillna(0).max()

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=agg_df["schedule_no"],
            y=agg_df["input_quantity"],
            name="Test-In QTY",
            yaxis="y",
            marker_color=MEDIUM_BLUE,
            opacity=0.50,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in agg_df["input_quantity"]],
            textposition="outside",
            textfont=dict(color="black", size=11),
            hovertemplate="Schedule No=%{x}<br>Test-In QTY=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Bar(
            x=agg_df["schedule_no"],
            y=agg_df["final_pass_qty"],
            name="Final Output",
            yaxis="y",
            marker_color=MEDIUM_LIGHT_GREEN,
            opacity=0.45,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in agg_df["final_pass_qty"]],
            textposition="outside",
            textfont=dict(color="black", size=11),
            hovertemplate="Schedule No=%{x}<br>Final Output=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=agg_df["schedule_no"],
            y=agg_df["first_pass_yield_pct"],
            mode="lines+markers+text",
            name="1st Yield",
            yaxis="y2",
            line=dict(color="#4F81BD", width=3),
            marker=dict(color="#4F81BD", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in agg_df["first_pass_yield_pct"]],
            textposition="top center",
            textfont=dict(color="black", size=10),
            hovertemplate="Schedule No=%{x}<br>1st Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=agg_df["schedule_no"],
            y=agg_df["final_yield_pct"],
            mode="lines+markers+text",
            name="Final Yield",
            yaxis="y2",
            line=dict(color="#92D050", width=3),
            marker=dict(color="#92D050", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in agg_df["final_yield_pct"]],
            textposition="top center",
            textfont=dict(color="black", size=10),
            hovertemplate="Schedule No=%{x}<br>Final Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_hline(
        y=selected_fty_target,
        yref="y2",
        line_dash="dash",
        line_color="red",
        annotation_text=f"FTY Target (3-sig LCL): {selected_fty_target:.2f}%",
        annotation_font_color="red"
    )

    fig.add_hline(
        y=selected_fpy_target,
        yref="y2",
        line_dash="dash",
        line_color="black",
        annotation_text=f"FPY Target (3-sig LCL): {selected_fpy_target:.2f}%",
        annotation_font_color="black"
    )

    fig.update_layout(
        title=f"{section_label} — Per Lot Yield Trend<br><sup>Date Range: {date_scope_label}</sup>",
        height=780,
        barmode="group",
        hovermode="x unified",
        dragmode="zoom",
        xaxis=dict(
            title="Schedule No",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Quantity",
            side="left",
            range=[0, max(10, round(float(max_qty) * 1.20, 0))]
        ),
        yaxis2=dict(
            title="Yield %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[auto_yield_min, auto_yield_max]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="left",
            x=0
        ),
        margin=dict(l=60, r=80, t=100, b=140)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = agg_df[[
        "schedule_no",
        "input_quantity",
        "final_pass_qty",
        "first_pass_yield_pct",
        "final_yield_pct"
    ]].copy()

    export_df = export_df.rename(columns={
        "schedule_no": "Schedule No",
        "input_quantity": "Test-In QTY",
        "final_pass_qty": "Final Output",
        "first_pass_yield_pct": "1st Yield",
        "final_yield_pct": "Final Yield"
    })

    return fig, export_df, date_scope_label

# =========================================================
# UPDATED ROW 1 RENDER
# =========================================================
def render_station_4week_yield_trend(
    df: pd.DataFrame,
    section_label: str,
    scope_key: str,
    full_daily_df: pd.DataFrame,
    device_code: str,
    station_value: str | None
):
    st.subheader("Yield Trend - Past 4 Weeks")

    if df.empty:
        st.info("No daily trend data available.")
        return None, pd.DataFrame()

    plot_df = build_4week_trend_display_df(df)

    if plot_df.empty:
        st.info("No 4-week trend data available.")
        return None, pd.DataFrame()

    # -----------------------------------------------------
    # Current-month FPY / FTY targets from back-month logic
    # -----------------------------------------------------
    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)

    current_fty_target = float(target_info.get("current_month_fty_target_lcl", 98.00))
    current_fpy_target = float(target_info.get("current_month_fpy_target_lcl", 95.00))

    st.caption(
        f"FTY Target (3-sig LCL): {current_fty_target:.2f}% | "
        f"FPY Target (3-sig LCL): {current_fpy_target:.2f}%"
    )

    plot_df["fty_target_lcl"] = current_fty_target
    plot_df["fpy_target_lcl"] = current_fpy_target

    yield_df = plot_df[["1st Yield", "Final Yield"]].apply(pd.to_numeric, errors="coerce")

    auto_yield_min = max(0.0, round(current_fpy_target - 3.0, 2))
    auto_yield_max = 101.0

    x_order = plot_df["x_label"].tolist()

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["Test-In QTY"],
            name="Test-In QTY",
            yaxis="y",
            marker_color=MEDIUM_BLUE,
            opacity=0.50,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in plot_df["Test-In QTY"]],
            textposition="outside",
            textfont=dict(color="black", size=12),
            hovertemplate="Bucket=%{x}<br>Test-In QTY=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["Final Output"],
            name="Final Output",
            yaxis="y",
            marker_color=MEDIUM_LIGHT_GREEN,
            opacity=0.45,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in plot_df["Final Output"]],
            textposition="outside",
            textfont=dict(color="black", size=12),
            hovertemplate="Bucket=%{x}<br>Final Output=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["1st Yield"],
            mode="lines+markers+text",
            name="1st Yield",
            yaxis="y2",
            line=dict(color="#4F81BD", width=3),
            marker=dict(color="#4F81BD", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["1st Yield"]],
            textposition="top center",
            textfont=dict(color="black", size=11),
            hovertemplate="Bucket=%{x}<br>1st Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["Final Yield"],
            mode="lines+markers+text",
            name="Final Yield",
            yaxis="y2",
            line=dict(color="#92D050", width=3),
            marker=dict(color="#92D050", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["Final Yield"]],
            textposition="top center",
            textfont=dict(color="black", size=11),
            hovertemplate="Bucket=%{x}<br>Final Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["fty_target_lcl"],
            mode="lines+markers+text",
            name="FTY Target (3-sig LCL)",
            yaxis="y2",
            line=dict(color="red", width=2, dash="dash"),
            marker=dict(color="red", size=6),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["fty_target_lcl"]],
            textposition="bottom center",
            textfont=dict(color="red", size=10),
            hovertemplate="Bucket=%{x}<br>FTY Target=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["fpy_target_lcl"],
            mode="lines+text",
            name="FPY Target (3-sig LCL)",
            yaxis="y2",
            line=dict(color="black", width=2, dash="dash"),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["fpy_target_lcl"]],
            textposition="bottom center",
            textfont=dict(color="black", size=10),
            hovertemplate="Bucket=%{x}<br>FPY Target=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    max_qty = pd.to_numeric(
        pd.concat([plot_df["Test-In QTY"], plot_df["Final Output"]]),
        errors="coerce"
    ).fillna(0).max()

    fig.update_layout(
        title=f"{section_label} — 4-Week Yield Trend",
        height=820,
        barmode="group",
        hovermode="x unified",
        font=dict(size=12, color="black"),
        dragmode="zoom",
        xaxis=dict(
            title="Week / Day",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Quantity",
            side="left",
            range=[0, max(10, round(float(max_qty) * 1.20, 0))]
        ),
        yaxis2=dict(
            title="Yield %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[auto_yield_min, auto_yield_max],
            fixedrange=False
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="left",
            x=0
        ),
        margin=dict(l=60, r=80, t=90, b=120)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = plot_df[[
        "x_label", "Test-In QTY", "Final Output", "1st Yield", "Final Yield"
    ]].copy()
    export_df = export_df.rename(columns={"x_label": "Date"})

    display_df = export_df.set_index("Date").T.reset_index().rename(columns={"index": "Metric"})

    if not display_df.empty and "Metric" in display_df.columns:
        qty_mask = display_df["Metric"].isin(["Test-In QTY", "Final Output"])
        for col in display_df.columns:
            if col != "Metric":
                display_df.loc[qty_mask, col] = display_df.loc[qty_mask, col].apply(
                    lambda x: int(float(x))
                    if pd.notna(x) and str(x).strip() not in {"", "-", "nan"}
                    else x
                )

    return fig, display_df

# =========================================================
# UPDATED ROW 2 RENDER
# =========================================================
def render_daily_summary_fty_errcode_chart(
    errcode_df: pd.DataFrame,
    daily_trend_df: pd.DataFrame,
    section_label: str,
    scope_key: str
):
    st.subheader("Top 5 Defect Rate Distribution")

    if errcode_df.empty:
        st.info("No FTY / errCode summary data available.")
        return None, pd.DataFrame()

    plot_df = build_4week_fty_errcode_chart_df(errcode_df, daily_trend_df)

    if plot_df.empty:
        st.info("No 4-week FTY / errCode summary data available.")
        return None, pd.DataFrame()

    max_defect = (
        plot_df[
            [
                "top1_fail_pct", "top2_fail_pct", "top3_fail_pct",
                "top4_fail_pct", "top5_fail_pct", "other_fail_pct"
            ]
        ]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
        .sum(axis=1)
        .max()
    )

    stack_df = plot_df[
        [
            "top1_fail_pct", "top2_fail_pct", "top3_fail_pct",
            "top4_fail_pct", "top5_fail_pct", "other_fail_pct"
        ]
    ].apply(pd.to_numeric, errors="coerce").fillna(0)

    max_defect_stack = stack_df.sum(axis=1).max()

    if pd.isna(max_defect_stack):
        max_defect_stack = 2

    auto_defect_min = 0
    auto_defect_max = max(1.5, round(float(max_defect_stack) * 1.25, 2))
    auto_defect_max = min(auto_defect_max, 100)

    x_order = plot_df["x_label"].tolist()

    # top1 changed away from blue/green
    color_map = {
        "top1_fail_pct": "#F79646",  # orange
        "top2_fail_pct": "#8064A2",  # purple
        "top3_fail_pct": "#C0504D",  # dark red
        "top4_fail_pct": "#4BACC6",  # teal
        "top5_fail_pct": "#7F7F7F",  # gray
        "other_fail_pct": "#D9D9D9", # light gray
    }

    label_map = {
        "top1_fail_pct": "Top1 errCode FTY",
        "top2_fail_pct": "Top2 errCode FTY",
        "top3_fail_pct": "Top3 errCode FTY",
        "top4_fail_pct": "Top4 errCode FTY",
        "top5_fail_pct": "Top5 errCode FTY",
        "other_fail_pct": "Other errCodes FTY",
    }

    hover_label_cols = {
        "top1_fail_pct": "top1_label",
        "top2_fail_pct": "top2_label",
        "top3_fail_pct": "top3_label",
        "top4_fail_pct": "top4_label",
        "top5_fail_pct": "top5_label",
    }

    fig = go.Figure()

    for fail_col in ["top1_fail_pct", "top2_fail_pct", "top3_fail_pct", "top4_fail_pct", "top5_fail_pct"]:
        fig.add_trace(
            go.Bar(
                x=plot_df["x_label"],
                y=plot_df[fail_col],
                name=label_map[fail_col],
                yaxis="y",
                marker_color=color_map[fail_col],
                opacity=0.85,
                customdata=plot_df[[hover_label_cols[fail_col]]].values,
                hovertemplate=(
                    "Bucket=%{x}<br>"
                    "ErrCode=%{customdata[0]}<br>"
                    "Fail %=%{y:.2f}%<extra></extra>"
                )
            )
        )

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["other_fail_pct"],
            name=label_map["other_fail_pct"],
            yaxis="y",
            marker_color=color_map["other_fail_pct"],
            opacity=0.85,
            hovertemplate="Bucket=%{x}<br>Other errCodes Fail %=%{y:.2f}%<extra></extra>"
        )
    )

    total_fail_pct = (
        plot_df[[
            "top1_fail_pct", "top2_fail_pct", "top3_fail_pct",
            "top4_fail_pct", "top5_fail_pct", "other_fail_pct"
        ]]
        .fillna(0)
        .sum(axis=1)
        .round(2)
    )

    # FPY line (blue format like yield trend row)
    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["FPY"],
            mode="lines+markers+text",
            name="FPY",
            yaxis="y2",
            line=dict(color="#4F81BD", width=3),
            marker=dict(color="#4F81BD", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["FPY"]],
            textposition="top center",
            textfont=dict(color="black", size=11),
            hovertemplate="Bucket=%{x}<br>FPY=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    # FTY line (green)
    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["FTY"],
            mode="lines+markers+text",
            name="FTY",
            yaxis="y2",
            line=dict(color="#92D050", width=4),
            marker=dict(color="#92D050", size=9),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["FTY"]],
            textposition="top center",
            textfont=dict(color="black", size=11),
            hovertemplate="Bucket=%{x}<br>FTY=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=total_fail_pct,
            mode="text",
            name="Total Defect %",
            yaxis="y",
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in total_fail_pct],
            textposition="top center",
            textfont=dict(color="black", size=11),
            hoverinfo="skip",
            showlegend=False,
            connectgaps=True
        )
    )

    yield_stack = plot_df[["FPY", "FTY"]].apply(pd.to_numeric, errors="coerce").stack().dropna()

    if yield_stack.empty:
        row2_yield_min = 90
        row2_yield_max = 100
    else:
        row2_yield_min = max(85, round(float(yield_stack.min()) - 1, 2))
        row2_yield_max = min(101, round(float(yield_stack.max()) + 1, 2))
        if row2_yield_max <= row2_yield_min:
            row2_yield_max = min(100, row2_yield_min + 2)

    fig.update_layout(
        title=f"{section_label} — Top 5 Defect Rate Distribution",
        height=820,
        barmode="stack",
        hovermode="x unified",
        font=dict(size=12, color="black"),
        dragmode="zoom",
        xaxis=dict(
            title="Week / Day",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Defect Rate %",
            range=[auto_defect_min, auto_defect_max]
        ),
        yaxis2=dict(
            title="Yield %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[row2_yield_min, row2_yield_max],
            fixedrange=False
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="left",
            x=0
        ),
        margin=dict(l=60, r=80, t=90, b=120)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = plot_df[[
        "x_label",
        "FTY",
        "top1_label", "top1_fail_pct",
        "top2_label", "top2_fail_pct",
        "top3_label", "top3_fail_pct",
        "top4_label", "top4_fail_pct",
        "top5_label", "top5_fail_pct",
        "other_fail_pct"
    ]].copy()

    export_df = export_df.rename(columns={"x_label": "Date"})

    display_df = combine_pct_and_label_for_display(
        export_df,
        label_pct_pairs=[
            ("top1_label", "top1_fail_pct"),
            ("top2_label", "top2_fail_pct"),
            ("top3_label", "top3_fail_pct"),
            ("top4_label", "top4_fail_pct"),
            ("top5_label", "top5_fail_pct"),
        ],
        other_pct_col="other_fail_pct",
        other_label="Other errCodes"
    )

    display_df = display_df.rename(columns={
        "FTY": "Overall_FTY",
        "top1_label": "top1_errCode",
        "top2_label": "top2_errCode",
        "top3_label": "top3_errCode",
        "top4_label": "top4_errCode",
        "top5_label": "top5_errCode",
        "other_fail_pct": "other_errCodes"
    })

    display_df = transpose_metric_table(display_df, date_col="Date")

    def format_errcode_label(label_text: str, metric_name: str) -> str:
        if pd.isna(label_text) or str(label_text).strip() in {"", "-", "nan"}:
            return metric_name

        s = str(label_text).strip()

        # remove leading percentage like "0.07% "
        s = re.sub(r"^\s*\d+(\.\d+)?%\s*", "", s)

        parts = s.split("\n")

        if len(parts) >= 2:
            sb = parts[0].strip()
            ec = parts[1].strip()
            return f"{metric_name} : {sb}\n{ec}"

        return f"{metric_name} : {s}"

    for i in range(1, 6):
        row_name = f"top{i}_errCode"

        if row_name in display_df["Metric"].values:
            idx = display_df["Metric"] == row_name

            label_val = None
            for col in display_df.columns:
                if col != "Metric":
                    val = display_df.loc[idx, col].values[0]
                    if pd.notna(val) and str(val).strip() not in {"", "-", "nan"}:
                        label_val = val
                        break

            display_df.loc[idx, "Metric"] = format_errcode_label(label_val, row_name)

    display_df = format_pct_rows_in_transposed_table(
        display_df,
        ["Overall_FTY"],
        decimals=2
    )

    for col in display_df.columns:
        if col == "Metric":
            continue

        display_df[col] = display_df[col].apply(
            lambda x: str(x).split(" ")[0] if isinstance(x, str) else x
        )
    st.dataframe(display_df, use_container_width=True, height=320)

    return fig, display_df

# =========================================================
# UPDATED ROW 3 RENDER
# =========================================================
def render_lrr_trend_chart(
    df: pd.DataFrame,
    scope_label: str,
    scope_key: str,
    device_code: str,
    station_value: str | None
):
    if df.empty:
        st.info("No LRR trend data available.")
        return None, pd.DataFrame()

    plot_df = build_4week_lrr_display_df(df)

    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)
    lrr_trigger_fty = float(target_info.get("current_month_fty_target_lcl", 98.00))

    if plot_df.empty:
        st.info("No LRR trend data available.")
        return None, pd.DataFrame()

    max_lrr = pd.to_numeric(plot_df["lrr_pct"], errors="coerce").max()

    if pd.isna(max_lrr):
        max_lrr = 2

    auto_lrr_min = 0
    auto_lrr_max = max(2, int((float(max_lrr) * 1.2) + 0.9999))
    auto_lrr_max = min(auto_lrr_max, 100)

    x_order = plot_df["x_label"].tolist()

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["total_lot_count"],
            name="Total Lots Tested",
            offsetgroup="total_lots",
            yaxis="y",
            marker_color=MEDIUM_BLUE,
            opacity=0.50,
            text=[f"<b>{int(v)}</b>" if pd.notna(v) else "" for v in plot_df["total_lot_count"]],
            textposition="outside",
            textfont=dict(color="black", size=12),
            hovertemplate="Bucket=%{x}<br>Total Lots=%{y}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["lrr_count"],
            name="LRR Count",
            offsetgroup="lrr_lots",
            yaxis="y",
            marker_color=LIGHT_RED,
            opacity=0.90,
            text=[f"<b>{int(v)}</b>" if pd.notna(v) else "" for v in plot_df["lrr_count"]],
            textposition="outside",
            textfont=dict(color="black", size=12),
            hovertemplate="Bucket=%{x}<br>LRR Count=%{y}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["lrr_pct"],
            mode="lines+markers+text",
            name="LRR %",
            yaxis="y2",
            line=dict(color="red", width=4),
            marker=dict(color="red", size=9),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["lrr_pct"]],
            textposition="top center",
            textfont=dict(color="black", size=12),
            hovertemplate="Bucket=%{x}<br>LRR %%=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    max_lot = pd.to_numeric(plot_df["total_lot_count"], errors="coerce").fillna(0).max()

    fig.update_layout(
        title=f"{scope_label} — LRR% Trend (4-Week Format) | LRR Trigger: FTY Target (3-sig LCL) @ {lrr_trigger_fty:.2f}%",
        title_font=dict(size=14, color="red"),
        height=650,
        font=dict(size=12, color="black"),
        barmode="group",
        hovermode="x unified",
        dragmode="zoom",
        xaxis=dict(
            title="Week / Day",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45,
            tickfont=dict(size=12, color="black"),
            title_font=dict(size=13, color="black")
        ),
        yaxis=dict(
            title="Lot Count",
            side="left",
            range=[0, max(5, round(float(max_lot) * 1.20, 0))],
            tickfont=dict(size=12, color="black"),
            title_font=dict(size=13, color="black")
        ),
        yaxis2=dict(
            title="LRR %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[auto_lrr_min, auto_lrr_max],
            tickfont=dict(size=12, color="black"),
            title_font=dict(size=13, color="black")
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=12, color="black")
        ),
        margin=dict(l=60, r=80, t=80, b=120)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = plot_df[[
        "x_label", "total_lot_count", "lrr_count", "lrr_pct", "lrr_lot_list"
    ]].copy()
    export_df = export_df.rename(columns={"x_label": "Date"})

    return fig, export_df

# =========================================================
# UPDATED ROW 5 RENDER
# =========================================================
def render_top10_rpr_errcode_pareto(df: pd.DataFrame, section_label: str, scope_key: str):
    st.subheader("Top 5 High Retest Pass Rate errCode Distribution")

    if df.empty:
        st.info("No retest pass rate / errCode summary data available.")
        return None, pd.DataFrame(), "All dates"

    filtered_df = add_date_range_slider(
        df,
        key_prefix=f"row5_rr_dist_4w_{scope_key}",
        label="Filter date range for Top 5 High Retest Pass Rate errCode Distribution",
        default_last_n_days_from_data_max=28
    )

    if filtered_df.empty:
        st.info("No data in selected date range.")
        return None, pd.DataFrame(), "No data"

    filtered_df = filtered_df.copy()
    filtered_df["test_date"] = pd.to_datetime(filtered_df["test_date"], errors="coerce")

    min_date = filtered_df["test_date"].min()
    max_date = filtered_df["test_date"].max()
    date_scope_label = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"

    plot_df = build_4week_retest_rate_errcode_chart_df(filtered_df)

    if plot_df.empty:
        st.info("No 4-week retest pass rate / errCode summary data available.")
        return None, pd.DataFrame(), date_scope_label

    # Primary axis = stacked bars
    max_rr_stack = (
        plot_df[
            [
                "top1_rr_pct", "top2_rr_pct", "top3_rr_pct",
                "top4_rr_pct", "top5_rr_pct", "other_rr_pct"
            ]
        ]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
        .sum(axis=1)
        .max()
    )

    if pd.isna(max_rr_stack):
        max_rr_stack = 2

    auto_rr_min = 0
    auto_rr_max = max(2, int((float(max_rr_stack) * 1.2) + 0.9999))
    auto_rr_max = min(auto_rr_max, 100)

    # Secondary axis = overall line
    max_rr_line = pd.to_numeric(plot_df["Retest Rate"], errors="coerce").max()

    if pd.isna(max_rr_line):
        max_rr_line = 2

    auto_rr2_min = 0
    auto_rr2_max = max(2, int((float(max_rr_line) * 1.2) + 0.9999))
    auto_rr2_max = min(auto_rr2_max, 100)

    x_order = plot_df["x_label"].tolist()

    color_map = {
        "top1_rr_pct": "#F79646",
        "top2_rr_pct": "#8064A2",
        "top3_rr_pct": "#C0504D",
        "top4_rr_pct": "#4BACC6",
        "top5_rr_pct": "#7F7F7F",
        "other_rr_pct": "#D9D9D9",
    }

    label_map = {
        "top1_rr_pct": "Top1 errCode RPR",
        "top2_rr_pct": "Top2 errCode RPR",
        "top3_rr_pct": "Top3 errCode RPR",
        "top4_rr_pct": "Top4 errCode RPR",
        "top5_rr_pct": "Top5 errCode RPR",
        "other_rr_pct": "Other errCodes RPR",
    }

    hover_label_cols = {
        "top1_rr_pct": "top1_label",
        "top2_rr_pct": "top2_label",
        "top3_rr_pct": "top3_label",
        "top4_rr_pct": "top4_label",
        "top5_rr_pct": "top5_label",
    }

    fig = go.Figure()

    for rr_col in ["top1_rr_pct", "top2_rr_pct", "top3_rr_pct", "top4_rr_pct", "top5_rr_pct"]:
        fig.add_trace(
            go.Bar(
                x=plot_df["x_label"],
                y=plot_df[rr_col],
                name=label_map[rr_col],
                yaxis="y",
                marker_color=color_map[rr_col],
                opacity=0.85,
                customdata=plot_df[[hover_label_cols[rr_col]]].values,
                hovertemplate=(
                    "Bucket=%{x}<br>"
                    "ErrCode=%{customdata[0]}<br>"
                    "RPR=%{y:.2f}%<extra></extra>"
                )
            )
        )

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["other_rr_pct"],
            name=label_map["other_rr_pct"],
            yaxis="y",
            marker_color=color_map["other_rr_pct"],
            opacity=0.85,
            hovertemplate="Bucket=%{x}<br>Other errCodes RPR=%{y:.2f}%<extra></extra>"
        )
    )

    total_bar_rr = (
        plot_df[[
            "top1_rr_pct", "top2_rr_pct", "top3_rr_pct",
            "top4_rr_pct", "top5_rr_pct", "other_rr_pct"
        ]]
        .fillna(0)
        .sum(axis=1)
        .round(2)
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=total_bar_rr,
            mode="lines+markers",
            name="Overall RPR",
            line=dict(color="red", width=3),
            marker=dict(color="red", size=8),
            hovertemplate="Bucket=%{x}<br>Overall RPR=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=total_bar_rr,
            mode="text",
            name="Total RPR",
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in total_bar_rr],
            textposition="top center",
            textfont=dict(color="black", size=12),
            hoverinfo="skip",
            showlegend=False,
            connectgaps=True
        )
    )

    fig.update_layout(
        title=f"{section_label} — Top 5 High Retest Pass Rate errCode Distribution<br><sup>Date Range: {date_scope_label}</sup>",
        height=820,
        barmode="stack",
        hovermode="x unified",
        font=dict(size=12, color="black"),
        dragmode="zoom",
        xaxis=dict(
            title="Week / Day",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Retest Pass Rate %",
            range=[auto_rr_min, auto_rr_max]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="left",
            x=0
        ),
        margin=dict(l=60, r=80, t=100, b=120)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = plot_df[[
        "x_label",
        "top1_label", "top1_rr_pct",
        "top2_label", "top2_rr_pct",
        "top3_label", "top3_rr_pct",
        "top4_label", "top4_rr_pct",
        "top5_label", "top5_rr_pct",
        "other_rr_pct"
    ]].copy()

    export_df = export_df.rename(columns={"x_label": "Date"})
    export_df["Overall_RPR"] = total_bar_rr

    display_df = combine_pct_and_label_for_display(
        export_df,
        label_pct_pairs=[
            ("top1_label", "top1_rr_pct"),
            ("top2_label", "top2_rr_pct"),
            ("top3_label", "top3_rr_pct"),
            ("top4_label", "top4_rr_pct"),
            ("top5_label", "top5_rr_pct"),
        ],
        other_pct_col="other_rr_pct",
        other_label="Other errCodes"
    )

    display_df = display_df.rename(columns={
        "top1_label": "top1_errCode",
        "top2_label": "top2_errCode",
        "top3_label": "top3_errCode",
        "top4_label": "top4_errCode",
        "top5_label": "top5_errCode",
        "other_rr_pct": "other_errCodes"
    })

    display_df = transpose_metric_table(display_df, date_col="Date")

    display_df = order_metric_rows(
        display_df,
        ["Overall_RPR", "top1_errCode", "top2_errCode", "top3_errCode", "top4_errCode", "top5_errCode", "other_errCodes"]
    )

    display_df = format_pct_rows_in_transposed_table(
        display_df,
        ["Overall_RPR"],
        decimals=2
    )

    def format_errcode_label(label_text: str, metric_name: str) -> str:
        if pd.isna(label_text) or str(label_text).strip() in {"", "-", "nan"}:
            return metric_name

        s = str(label_text).strip()

        # remove leading percentage like "0.82% "
        s = re.sub(r"^\s*\d+(\.\d+)?%\s*", "", s)

        parts = s.split("\n")

        if len(parts) >= 2:
            sb = parts[0].strip()
            ec = parts[1].strip()
            return f"{metric_name} : {sb}\n{ec}"

        return f"{metric_name} : {s}"


    for i in range(1, 6):
        row_name = f"top{i}_errCode"

        if row_name in display_df["Metric"].values:
            idx = display_df["Metric"] == row_name

            label_val = None
            for col in display_df.columns:
                if col != "Metric":
                    val = display_df.loc[idx, col].values[0]
                    if pd.notna(val) and str(val).strip() not in {"", "-", "nan"}:
                        label_val = val
                        break

            display_df.loc[idx, "Metric"] = format_errcode_label(label_val, row_name)


    for col in display_df.columns:
        if col == "Metric":
            continue

        display_df[col] = display_df[col].apply(
            lambda x: re.search(r"\d+(\.\d+)?%", str(x)).group(0)
            if pd.notna(x) and re.search(r"\d+(\.\d+)?%", str(x))
            else x
        )

    st.dataframe(display_df, use_container_width=True, height=320)

    return fig, display_df, date_scope_label

# =========================================================
# UPDATED ROW 6 LEFT
# =========================================================
def render_handler_top5_rpr_chart(df: pd.DataFrame, section_label: str, scope_key: str):
    st.subheader("Handler vs Top 5 High RPR errCode")

    if df.empty:
        st.info("No handler RPR data available.")
        return None, pd.DataFrame(), "No data"

    filtered_df, date_scope_label = filter_previous_day_only(df)

    if filtered_df.empty:
        st.info("No previous-day handler RPR data available.")
        return None, pd.DataFrame(), date_scope_label

    filtered_df = filtered_df.copy()
    filtered_df["test_date"] = pd.to_datetime(filtered_df["test_date"], errors="coerce")

    min_date = filtered_df["test_date"].min()
    max_date = filtered_df["test_date"].max()
    date_scope_label = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"

    plot_df = build_group_top5_rpr_df(filtered_df, group_cols=["handler"])

    if plot_df.empty:
        st.info("No handler RPR data available.")
        return None, pd.DataFrame(), date_scope_label

    y_min, y_max = st.slider(
        "Adjust Handler RPR axis range (%)",
        min_value=0,
        max_value=100,
        value=(0, 100),
        step=1,
        key=f"handler_rpr_axis_{scope_key}"
    )

    palette = get_rpr_stack_palette()
    plot_df = plot_df.sort_values(["total_rpr_pct", "group_name"], ascending=[False, True]).reset_index(drop=True)
    order = plot_df["group_name"].tolist()

    stacked_max = float(plot_df["total_rpr_pct"].max()) if not plot_df.empty else 0.0
    auto_y_max = max(10, round(stacked_max * 1.25, 2))

    fig = go.Figure()

    for idx, key in enumerate(["top1", "top2", "top3", "top4", "top5"], start=1):
        fig.add_trace(
            go.Bar(
                x=plot_df["group_name"],
                y=plot_df[f"{key}_rpr_pct"],
                name=f"Top{idx}",
                marker_color=palette[key],
                opacity=0.85,
                customdata=plot_df[[f"{key}_errCode"]].values,
                hovertemplate=(
                    "Handler=%{x}<br>"
                    "errCode=%{customdata[0]}<br>"
                    "RPR=%{y:.2f}%<extra></extra>"
                )
            )
        )

    fig.add_trace(
        go.Bar(
            x=plot_df["group_name"],
            y=plot_df["other_rpr_pct"],
            name="Other",
            marker_color=palette["other"],
            opacity=0.85,
            hovertemplate="Handler=%{x}<br>Other errCodes RPR=%{y:.2f}%<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["group_name"],
            y=plot_df["total_rpr_pct"],
            mode="text",
            text=[f"{v:.2f}%" for v in plot_df["total_rpr_pct"]],
            textposition="top center",
            textfont=dict(color="black", size=11),
            showlegend=False,
            hoverinfo="skip",
            connectgaps=True
        )
    )

    fig.update_layout(
        title=f"{section_label} — Handler vs Top 5 High RPR errCode<br><sup>Date Range: {date_scope_label}</sup>",
        height=820,
        barmode="stack",
        hovermode="x unified",
        font=dict(size=12, color="black"),
        xaxis=dict(
            title="Handler",
            type="category",
            categoryorder="array",
            categoryarray=order,
            tickangle=-45
        ),
        yaxis=dict(
            title="RPR %",
            range=[y_min, auto_y_max if y_max == 100 else y_max]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0
        ),
        margin=dict(l=60, r=40, t=100, b=140)
    )

    st.plotly_chart(fig, use_container_width=True)

    display_df = plot_df.copy()
    display_df = display_df.rename(columns={"group_name": "Handler #"})

    # ------------------------------------------
    # Rename + format qty columns for HTML export
    # ------------------------------------------
    display_df = display_df.rename(columns={
        "group_total_input_qty": "input_qty",
        "group_total_recovered_qty": "rpr_qty"
    })

    for col in ["input_qty", "rpr_qty"]:
        if col in display_df.columns:
            display_df[col] = pd.to_numeric(display_df[col], errors="coerce").apply(
                lambda x: int(x) if pd.notna(x) else x
            )

    display_df = combine_pct_and_label_for_display(
        display_df,
        label_pct_pairs=[
            ("top1_errCode", "top1_rpr_pct"),
            ("top2_errCode", "top2_rpr_pct"),
            ("top3_errCode", "top3_rpr_pct"),
            ("top4_errCode", "top4_rpr_pct"),
            ("top5_errCode", "top5_rpr_pct"),
        ],
        other_pct_col="other_rpr_pct",
        other_label="Other errCodes"
    )

    display_df = display_df.drop(columns=["other_errCodes"], errors="ignore")

    display_df = display_df.rename(columns={
        "total_rpr_pct": "Total_RPR",
        "other_rpr_pct": "other_errCodes"
    })

    # ✅ Apply percentage formatting to column
    if "Total_RPR" in display_df.columns:
        display_df["Total_RPR"] = display_df["Total_RPR"].apply(
            lambda x: format_pct_value(x, decimals=2)
        )

    st.dataframe(display_df, use_container_width=True, height=320)

    return fig, display_df, date_scope_label


# =========================================================
# UPDATED ROW 6 RIGHT
# =========================================================
def render_handler_site_top5_rpr_chart(df: pd.DataFrame, section_label: str, scope_key: str):
    st.subheader("Top 5 Handler-Site vs Top 5 High RPR errCode")

    if df.empty:
        st.info("No handler-site RPR data available.")
        return None, pd.DataFrame(), "No data", []

    filtered_df, date_scope_label = filter_previous_day_only(df)

    if filtered_df.empty:
        st.info("No previous-day handler-site RPR data available.")
        return None, pd.DataFrame(), date_scope_label, []

    filtered_df = filtered_df.copy()
    filtered_df["test_date"] = pd.to_datetime(filtered_df["test_date"], errors="coerce")

    min_date = filtered_df["test_date"].min()
    max_date = filtered_df["test_date"].max()
    date_scope_label = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"

    plot_df = build_group_top5_rpr_df(filtered_df, group_cols=["handler", "site"], top_n_groups=5)

    if plot_df.empty:
        st.info("No handler-site RPR data available.")
        return None, pd.DataFrame(), date_scope_label, []

    x_min, x_max = st.slider(
        "Adjust Handler-Site RPR axis range (%)",
        min_value=0,
        max_value=100,
        value=(0, 100),
        step=1,
        key=f"handler_site_rpr_axis_{scope_key}"
    )

    palette = get_rpr_stack_palette()
    plot_df = plot_df.sort_values(["total_rpr_pct", "group_name"], ascending=[False, True]).reset_index(drop=True)
    order = plot_df.sort_values(["total_rpr_pct", "group_name"], ascending=[True, True])["group_name"].tolist()

    stacked_max = float(plot_df["total_rpr_pct"].max()) if not plot_df.empty else 0.0
    auto_x_max = max(10, round(stacked_max * 1.25, 2))

    fig = go.Figure()

    for key in ["top1", "top2", "top3", "top4", "top5"]:
        fig.add_trace(
            go.Bar(
                y=plot_df["group_name"],
                x=plot_df[f"{key}_rpr_pct"],
                name=f"{key.upper()}",
                orientation="h",
                marker_color=palette[key],
                opacity=0.80,
                customdata=plot_df[[f"{key}_errCode"]].values,
                hovertemplate=(
                    "Handler-Site=%{y}<br>"
                    "errCode=%{customdata[0]}<br>"
                    "RPR%%=%{x:.2f}%<extra></extra>"
                )
            )
        )

    fig.add_trace(
        go.Bar(
            y=plot_df["group_name"],
            x=plot_df["other_rpr_pct"],
            name="OTHER",
            orientation="h",
            marker_color=palette["other"],
            opacity=0.80,
            hovertemplate="Handler-Site=%{y}<br>Other errCodes RPR=%{x:.2f}%<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            y=plot_df["group_name"],
            x=plot_df["total_rpr_pct"],
            mode="text",
            text=[f"{v:.2f}%" for v in plot_df["total_rpr_pct"]],
            textposition="middle right",
            textfont=dict(color="black", size=11),
            showlegend=False,
            hoverinfo="skip",
            connectgaps=True
        )
    )

    fig.update_layout(
        title=f"{section_label} — Top 5 Handler-Site vs Top 5 High RPR<br><sup>Date Range: {date_scope_label}</sup>",
        height=900,
        barmode="stack",
        hovermode="y unified",
        font=dict(size=12, color="black"),
        xaxis=dict(
            title="RPR %",
            range=[x_min, auto_x_max if x_max == 100 else x_max]
        ),
        yaxis=dict(
            title="Handler | Site",
            categoryorder="array",
            categoryarray=order
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0
        ),
        margin=dict(l=140, r=80, t=100, b=80)
    )

    st.plotly_chart(fig, use_container_width=True)

    # NEW: show 7-day time series charts for Top 10 Handler-Site contributors
    handler_site_7day_figs = render_handler_site_7day_timeseries_charts(
        source_df=df,   # use full source, not previous-day filtered_df
        top10_df=plot_df,
        section_label=section_label,
        scope_key=scope_key
    )

    display_df = combine_pct_and_label_for_display(
        plot_df.rename(columns={
            "group_name": "Handler-Site#"
        }),
        label_pct_pairs=[
            ("top1_errCode", "top1_rpr_pct"),
            ("top2_errCode", "top2_rpr_pct"),
            ("top3_errCode", "top3_rpr_pct"),
            ("top4_errCode", "top4_rpr_pct"),
            ("top5_errCode", "top5_rpr_pct"),
        ],
        other_pct_col="other_rpr_pct",
        other_label="Other errCodes"
    )

    # ------------------------------------------
    # Rename + format qty columns for HTML export
    # ------------------------------------------
    display_df = display_df.rename(columns={
        "group_total_input_qty": "input_qty",
        "group_total_recovered_qty": "rpr_qty"
    })

    for col in ["input_qty", "rpr_qty"]:
        if col in display_df.columns:
            display_df[col] = pd.to_numeric(display_df[col], errors="coerce").apply(
                lambda x: int(x) if pd.notna(x) else x
            )

    if not display_df.empty and "total_rpr_pct" in display_df.columns:
        display_df["total_rpr_pct"] = display_df["total_rpr_pct"].apply(
            lambda x: f"{float(x):.2f}%"
            if pd.notna(x) and str(x).strip() not in {"", "-", "nan"}
            else x
        )

    display_df = display_df.rename(columns={
        "total_rpr_pct": "Total_RPR"
    })

    display_df = format_pct_columns(
        display_df,
        ["Total_RPR"],
        decimals=2
    )

    # These are moved back to the left so they run even if the IF condition isn't met
    st.dataframe(display_df, use_container_width=True, height=320)

    return fig, display_df, date_scope_label, handler_site_7day_figs


# =========================================================
# UPDATED EXPORT HTML
# =========================================================
def build_station_html_report(
    station_label: str,
    day_fig,
    summary_fig,
    lrr_fig,
    mother_lot_fig,
    schedule_no_fig,
    top_fail_fig,
    retest_fail_fig,
    first_pass_fig,
    row5_date_scope: str,
    row6_left_date_scope: str,
    row6_right_date_scope: str,
    daily_trend_df: pd.DataFrame,
    summary_chart_df: pd.DataFrame,
    lrr_summary_df: pd.DataFrame,
    mother_lot_table_df: pd.DataFrame,
    schedule_no_table_df: pd.DataFrame,
    top_fail_table_df: pd.DataFrame,
    retest_fail_table_df: pd.DataFrame,
    first_pass_table_df: pd.DataFrame,
    show_tables: bool = True,
    kpi_cards_html: str = "",
    kpi_target_labels_html: str = "",
    handler_site_7day_figs=None,
) -> str:

    def safe_float(v):
        try:
            if pd.isna(v):
                return None
            if isinstance(v, str):
                s = v.strip()
                if s in {"", "-", "nan", "None"}:
                    return None
                if s.endswith("%"):
                    s = s[:-1].strip()
                return float(s)
            return float(v)
        except Exception:
            return None

    def format_cell(value, row_metric=None, col_name=None):
        if pd.isna(value):
            return "-"

        # preserve strings that already contain line breaks / labels
        if isinstance(value, str):
            s = value.strip()
            if s in {"", "nan", "None"}:
                return "-"
            if "<br>" in s or "\n" in s:
                return s.replace("\n", "<br>")
        else:
            s = str(value)

        int_metrics = {"Test-In QTY", "Final Output", "Total Lots Tested", "LRR Count"}
        pct_metrics = {"Overall_FTY", "Overall_RPR", "LRR%", "FTY", "FPY", "RPR", "LRR"}

        int_cols = {"Test-In QTY", "Final Output", "Total Lots Tested", "LRR Count"}
        pct_cols = {"LRR%"}

        f = safe_float(value)

        # For transposed tables: format based on Metric row
        if row_metric in int_metrics and f is not None:
            return f"{int(round(f))}"

        if row_metric in pct_metrics and f is not None:
            return f"{f:.2f}%"

        # For regular tables: format based on column name
        if col_name in int_cols and f is not None:
            return f"{int(round(f))}"

        if col_name in pct_cols and f is not None:
            return f"{f:.2f}%"

        # If already a clean percentage string, keep it
        if isinstance(value, str) and value.strip().endswith("%"):
            return value.replace("\n", "<br>")

        return s.replace("\n", "<br>")

    def table_html(df: pd.DataFrame, title: str) -> str:
        if df is None or df.empty:
            return f"<h3>{title}</h3><p>No data available.</p>"

        out = df.copy()

        if "x_label" in out.columns:
            out = out.rename(columns={"x_label": "Date"})

        # Special cleanup for LRR summary normal table
        rename_map = {}
        if "mother_lot_list" in out.columns:
            rename_map["mother_lot_list"] = "Mother lot"
        if "total_lot_count" in out.columns:
            rename_map["total_lot_count"] = "Total Lots Tested"
        if "lrr_count" in out.columns:
            rename_map["lrr_count"] = "LRR Count"
        if "lrr_pct" in out.columns:
            rename_map["lrr_pct"] = "LRR%"
        if "lrr_lot_list" in out.columns:
            rename_map["lrr_lot_list"] = "LRR Failed Lots"
        if rename_map:
            out = out.rename(columns=rename_map)

        # Build formatted HTML manually so we control every cell
        headers = "".join(f"<th>{col}</th>" for col in out.columns)

        body_rows = []
        for _, row in out.iterrows():
            row_metric = row["Metric"] if "Metric" in out.columns else None
            cells = []
            for col in out.columns:
                formatted = format_cell(row[col], row_metric=row_metric, col_name=col)
                cells.append(f"<td>{formatted}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

        return f"""
        <h3>{title}</h3>
        <table border="1">
            <thead>
                <tr>{headers}</tr>
            </thead>
            <tbody>
                {''.join(body_rows)}
            </tbody>
        </table>
        """

    day_table_html = table_html(daily_trend_df, "4-Week Yield Trend Data")
    summary_table_html = table_html(summary_chart_df, "Top 5 Defect Rate Distribution Data")
    lrr_summary_table_html = table_html(lrr_summary_df, "LRR Summary Data")
    mother_lot_table_html = table_html(mother_lot_table_df, "Mother Lot Yield Trend Data")
    schedule_no_table_html = table_html(schedule_no_table_df, "Per Lot Yield Trend Data")
    top_fail_table_html = table_html(top_fail_table_df, "Top 5 High Retest Pass Rate errCode Distribution Data")
    retest_fail_table_html = table_html(retest_fail_table_df, "Handler vs Top 5 High RPR Data")
    first_pass_table_html = table_html(first_pass_table_df, "Handler-Site vs Top5 High RPR Data")

    handler_site_7day_html = ""

    if handler_site_7day_figs:
        handler_site_7day_html = "<h2>Top 5 Handler-Site 7-Day RPR Time Series</h2>"
        for fig in handler_site_7day_figs:
            handler_site_7day_html += fig_to_html_fragment(fig)

    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>SIP Yield Dashboard - {station_label}</title>
        <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 24px;
                color: black;
                background: white;
            }}
            h1, h2, h3 {{
                color: black;
            }}
            .section {{
                margin-bottom: 40px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin-top: 12px;
            }}
            th, td {{
                border: 1px solid #d9d9d9;
                padding: 8px;
                text-align: center;
                vertical-align: middle;
                white-space: pre-line;
            }}
            th {{
                background: #eef4fb;
            }}
        </style>
    </head>
    <body>
        <h1>SIP Yield Dashboard - {station_label}</h1>
        <p>Exported from Streamlit dashboard</p>

        <div class="section">
            {kpi_target_labels_html}
            {kpi_cards_html}
        </div>

        <div class="section">
            <h2>4-Week Yield Trend</h2>
            {fig_to_html_fragment(day_fig)}
            {day_table_html}
        </div>

        <div class="section">
            <h2>Top5 Defect Rate Distribution</h2>
            {fig_to_html_fragment(summary_fig)}
            {summary_table_html}
        </div>

        <div class="section">
            <h2>Mother Lot Yield Trend</h2>
            {fig_to_html_fragment(mother_lot_fig)}
            {mother_lot_table_html}
        </div>

        <div class="section">
            <h2>Per Lot Yield Trend</h2>
            {fig_to_html_fragment(schedule_no_fig)}
            {schedule_no_table_html}
        </div>

        <div class="section">
            <h2>LRR Trend</h2>
            {fig_to_html_fragment(lrr_fig)}
        </div>

        <div class="section">
            <h2>LRR Summary</h2>
            {lrr_summary_table_html}
        </div>

        <div class="section">
            <h2>Top 5 High Retest Pass Rate errCode Distribution</h2>
            <p><b>Date Range:</b> {row5_date_scope}</p>
            {fig_to_html_fragment(top_fail_fig)}
            {top_fail_table_html}
        </div>

        <div class="section">
            <h2>Handler vs Top 5 High RPR</h2>
            <p><b>Date Range:</b> {row6_left_date_scope}</p>
            {fig_to_html_fragment(retest_fail_fig)}
            {retest_fail_table_html}
        </div>

        <div class="section">
            <h2>Handler-Site vs Top 5 High RPR</h2>
            <p><b>Date Range:</b> {row6_right_date_scope}</p>
            {fig_to_html_fragment(first_pass_fig)}
            {first_pass_table_html}
            {handler_site_7day_html}
        </div>
    </body>
    </html>
    """
    return html

def render_lrr_summary_table(raw_summary_df: pd.DataFrame):
    if raw_summary_df.empty:
        return pd.DataFrame()

    display_df = build_4week_lrr_display_df(raw_summary_df)

    if display_df.empty:
        st.info("No LRR summary data available.")
        return pd.DataFrame()

    light_border = "#d9d9d9"
    header_blue = "rgba(79,129,189,0.18)"
    metric_col_blue = "rgba(79,129,189,0.10)"
    white_bg = "#ffffff"

    html = []
    html.append("<h3>LRR Summary Table</h3>")
    html.append("<p><b>4-week format</b></p>")
    html.append(f"<table style='border-collapse:collapse; width:100%; border:1px solid {light_border};'>")

    html.append("<thead><tr>")
    html.append(
        f"<th style='border:1px solid {light_border}; padding:10px; font-size:17px; "
        f"font-weight:700; background-color:{header_blue}; color:black;'>Metric</th>"
    )
    for col in display_df["x_label"].tolist():
        html.append(
            f"<th style='border:1px solid {light_border}; padding:10px; font-size:17px; "
            f"font-weight:700; background-color:{header_blue}; color:black; text-align:center;'>{col}</th>"
        )
    html.append("</tr></thead>")

    metric_rows = {
        "Mother lot": display_df["mother_lot_list"].tolist(),
        "Total Lots Tested": display_df["total_lot_count"].tolist(),
        "LRR Count": display_df["lrr_count"].tolist(),
        "LRR%": display_df["lrr_pct"].tolist(),
        "LRR Failed Lots": display_df["lrr_lot_list"].tolist(),
    }

    html.append("<tbody>")
    for row_label, row_values in metric_rows.items():
        html.append("<tr>")
        html.append(
            f"<td style='border:1px solid {light_border}; padding:10px; font-size:16px; "
            f"font-weight:700; background-color:{metric_col_blue}; color:black; "
            f"text-align:left; vertical-align:middle;'>{row_label}</td>"
        )

        for val in row_values:
            if row_label == "LRR%":
                text = f"{val:.2f}%" if pd.notna(val) else "-"
                if pd.notna(val) and float(val) > 0:
                    text = f"<span style='color:red;'>{text}</span>"
            elif row_label in ["Mother lot", "LRR Failed Lots"]:
                if pd.isna(val):
                    text = "-"
                else:
                    items = [x.strip() for x in str(val).split("\n") if x.strip() and x.strip() != "-"]
                    text = "<br>".join(items) if items else "-"
            else:
                text = f"{int(val)}" if pd.notna(val) else "-"

            html.append(
                f"<td style='border:1px solid {light_border}; padding:10px; font-size:15px; "
                f"color:black; background-color:{white_bg}; text-align:center; "
                f"vertical-align:top; line-height:1.5;'>{text}</td>"
            )

        html.append("</tr>")
    html.append("</tbody></table>")

    st.markdown("".join(html), unsafe_allow_html=True)

    export_df = display_df[[
        "x_label",
        "mother_lot_list",
        "total_lot_count",
        "lrr_count",
        "lrr_pct",
        "lrr_lot_list"
    ]].copy()

    export_df = export_df.rename(columns={
        "x_label": "Date",
        "mother_lot_list": "Mother lot",
        "total_lot_count": "Total Lots Tested",
        "lrr_count": "LRR Count",
        "lrr_pct": "LRR%",
        "lrr_lot_list": "LRR Failed Lots"
    })

    for col in ["Total Lots Tested", "LRR Count"]:
        if col in export_df.columns:
            export_df[col] = pd.to_numeric(export_df[col], errors="coerce").apply(
                lambda x: int(x) if pd.notna(x) else x
            )

    return export_df

def get_lrr_summary_export_df(raw_summary_df: pd.DataFrame) -> pd.DataFrame:
    if raw_summary_df is None or raw_summary_df.empty:
        return pd.DataFrame()

    df = build_4week_lrr_display_df(raw_summary_df)
    if df.empty:
        return pd.DataFrame()

    out = df[[
        "x_label",
        "mother_lot_list",
        "total_lot_count",
        "lrr_count",
        "lrr_pct",
        "lrr_lot_list"
    ]].copy()

    out = out.rename(columns={
        "x_label": "Date",
        "mother_lot_list": "Mother lot",
        "total_lot_count": "Total Lots Tested",
        "lrr_count": "LRR Count",
        "lrr_pct": "LRR%",
        "lrr_lot_list": "LRR Failed Lots"
    })

    for col in ["Total Lots Tested", "LRR Count"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").apply(
                lambda x: int(x) if pd.notna(x) else x
            )

    return out

# =========================================================
# PERIOD HTML EXPORT
# =========================================================
def build_period_html_report(
    section_label: str,
    period_label: str,
    header_html: str,
    yield_fig,
    defect_fig,
) -> str:
    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>SIP {period_label} Dashboard - {section_label}</title>
        <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 24px;
                color: black;
                background: white;
            }}
            h1, h2, h3 {{
                color: black;
            }}
            .section {{
                margin-bottom: 40px;
            }}
        </style>
    </head>
    <body>
        <h1>SIP {period_label} Dashboard - {section_label}</h1>

        <div class="section">
            {header_html}
        </div>

        <div class="section">
            <h2>Yield Trend - {period_label}</h2>
            {fig_to_html_fragment(yield_fig)}
        </div>

        <div class="section">
            <h2>Top5 Defect Rate Distribution - {period_label}</h2>
            {fig_to_html_fragment(defect_fig)}
        </div>
    </body>
    </html>
    """

def build_period_tab_html_report(period_label: str, rendered_sections: list[dict]) -> str:
    def table_html(df: pd.DataFrame, title: str) -> str:
        if df is None or df.empty:
            return f"<h3>{title}</h3><p>No data available.</p>"

        out = df.copy()

        headers = "".join(f"<th>{col}</th>" for col in out.columns)

        body_rows = []
        for _, row in out.iterrows():
            cells = []
            for col in out.columns:
                val = row[col]
                if pd.isna(val):
                    text = "-"
                else:
                    text = str(val).replace("\n", "<br>")
                cells.append(f"<td>{text}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

        return f"""
        <h3>{title}</h3>
        <table border="1">
            <thead>
                <tr>{headers}</tr>
            </thead>
            <tbody>
                {''.join(body_rows)}
            </tbody>
        </table>
        """

    body_sections = []

    for item in rendered_sections:
        section_label = item["section_label"]
        yield_fig = item["yield_fig"]
        defect_fig = item["defect_fig"]
        defect_table_df = item.get("defect_table_df", pd.DataFrame())
        target_labels_html = item.get("target_labels_html", "")

        if target_labels_html is None or str(target_labels_html).strip().lower() in {"", "none", "nan", "undefined"}:
            target_labels_html = ""

        defect_table_html = table_html(
            defect_table_df,
            f"{section_label} Top 5 Defect Rate Distribution Data"
        )

        body_sections.append(f"""
        <div class="section">
            <h2>{section_label} Yield Trend - {period_label}</h2>
            {fig_to_html_fragment(yield_fig)}
        </div>

        <div class="section">
            <h2>{section_label} Top 5 Defect Rate Distribution - {period_label}</h2>
            {fig_to_html_fragment(defect_fig)}
            {defect_table_html}
        </div>
        """)

    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>SIP {period_label} Dashboard</title>
        <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 24px;
                color: black;
                background: white;
            }}
            h1, h2, h3 {{
                color: black;
            }}
            .section {{
                margin-bottom: 42px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin-top: 12px;
            }}
            th, td {{
                border: 1px solid #d9d9d9;
                padding: 8px;
                text-align: center;
                vertical-align: middle;
                white-space: pre-line;
            }}
            th {{
                background: #eef4fb;
            }}
        </style>
    </head>
    <body>
        <h1>SIP {period_label} Dashboard</h1>
        {''.join(body_sections)}
    </body>
    </html>
    """

# =========================================================
# UPDATED RENDER SECTION
# =========================================================
def render_scope_section(device_code: str, station_value: str | None, section_label: str):
    st.header(section_label)

    scope_key = f"{device_code}_{station_value or 'NA'}"
    lot_options = get_lot_list_by_scope(device_code, station_value)

    top1, top2 = st.columns([2, 1])
    with top1:
        selected_lot = st.selectbox(
            f"Select schedule for pareto — {section_label}",
            options=lot_options,
            index=0,
            key=f"lot_filter_{scope_key}"
        )
    with top2:
        show_tables = st.checkbox(
            "Show trend tables",
            value=True,
            key=f"show_tables_{scope_key}"
        )

    daily_trend_df = run_query(get_station_daily_trend_sql(device_code, station_value))
    daily_summary_raw_df = run_query(get_daily_summary_table_sql(device_code, station_value))
    daily_summary_errcode_df = run_query(get_daily_summary_errcode_chart_sql(device_code, station_value))
    mother_lot_trend_df = run_query(get_mother_lot_yield_sql(device_code, station_value))
    schedule_no_trend_df = run_query(get_schedule_no_yield_sql(device_code, station_value))

    row5_rpr_errcode_df = run_query(
        get_top10_rpr_errcode_pareto_sql(device_code, station_value, selected_lot)
    )

    row6_handler_rpr_df = run_query(
        get_handler_rpr_distribution_sql(device_code, station_value, selected_lot)
    )

    st.divider()

    # NEW TOP KPI ROW
    kpi_cards_html, kpi_export_df, kpi_target_labels_html = render_kpi_cards(
        device_code=device_code,
        station_value=station_value,
        daily_trend_df=daily_trend_df,
        daily_summary_raw_df=daily_summary_raw_df,
        section_label=section_label
    )

    if not kpi_export_df.empty:
        kpi_display_df = kpi_export_df.copy()
        kpi_display_df = format_pct_columns(
            kpi_display_df,
            ["FTY", "FPY", "RPR", "LRR"],
            decimals=2
        )
        for c in ["Input QTY", "Output QTY"]:
            if c in kpi_display_df.columns:
                kpi_display_df[c] = kpi_display_df[c].apply(
                    lambda x: f"{int(x):,}" if pd.notna(x) else "-"
                )

        st.dataframe(kpi_display_df, use_container_width=True, height=220)

    st.divider()

    full_daily_df = get_kpi_target_source_df(device_code, station_value)
    target_info = get_month_fty_targets_from_history(full_daily_df)

    if not daily_summary_raw_df.empty:
        daily_summary_raw_df = daily_summary_raw_df.copy()
        daily_summary_raw_df["test_date"] = pd.to_datetime(daily_summary_raw_df["test_date"], errors="coerce")
        daily_summary_raw_df["fty_target"] = daily_summary_raw_df["test_date"].apply(
            lambda x: get_fty_target_for_date(x, target_info)
        )

    # ROW 1
    day_fig, day_export_df = render_station_4week_yield_trend(
        daily_trend_df,
        section_label,
        scope_key,
        full_daily_df=full_daily_df,
        device_code=device_code,
        station_value=station_value
    )

    if show_tables:
        st.dataframe(day_export_df, use_container_width=True, height=260)

    st.divider()

    # ROW 2
    summary_fig, summary_export_df = render_daily_summary_fty_errcode_chart(
        daily_summary_errcode_df,
        daily_trend_df,
        section_label,
        scope_key
    )

    st.divider()

    # NEW ROW - Mother Lot Yield Trend
    mother_lot_fig, mother_lot_export_df, mother_lot_date_scope = render_mother_lot_yield_trend(
        mother_lot_trend_df,
        section_label,
        scope_key,
        full_daily_df=full_daily_df,
        device_code=device_code,
        station_value=station_value
    )

    if show_tables:
        st.dataframe(mother_lot_export_df, use_container_width=True, height=320)

    st.divider()

    # NEW ROW - Per Lot (Schedule No) Yield Trend
    schedule_no_fig, schedule_no_export_df, schedule_no_date_scope = render_schedule_no_yield_trend(
        schedule_no_trend_df,
        section_label,
        scope_key,
        full_daily_df=full_daily_df,
        device_code=device_code,
        station_value=station_value
    )

    if show_tables:
        st.dataframe(schedule_no_export_df, use_container_width=True, height=320)

    st.divider()

    # ROW 3
    lrr_fig, lrr_export_df = render_lrr_trend_chart(
        daily_summary_raw_df,
        section_label,
        scope_key,
        device_code=device_code,
        station_value=station_value
    )

    st.divider()

    # ROW 4
    lrr_summary_export_df = get_lrr_summary_export_df(daily_summary_raw_df)

    if not lrr_summary_export_df.empty:
        st.subheader("LRR Summary Table")
        st.dataframe(lrr_summary_export_df, use_container_width=True, height=320)
    else:
        st.info("No LRR summary data available.")

    st.divider()

    # ROW 5
    row5_fig, row5_export_df, row5_date_scope = render_top10_rpr_errcode_pareto(
        row5_rpr_errcode_df,
        section_label,
        scope_key
    )

    st.divider()

    # ROW 6
    row6_fig, row6_export_df, row6_date_scope = render_handler_top5_rpr_chart(
        row6_handler_rpr_df,
        section_label,
        scope_key
    )

    st.divider()

    # ROW 7
    row7_fig, row7_export_df, row7_date_scope, row7_7day_figs = render_handler_site_top5_rpr_chart(
        row6_handler_rpr_df,
        section_label,
        scope_key
    )

    st.divider()

    EXPORT_DIR = BASE_DIR / "exports"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # -----------------------------------------------------
        # Prepare clean HTML export tables
        # -----------------------------------------------------
        html_day_export_df = day_export_df.copy() if day_export_df is not None else pd.DataFrame()

        if not html_day_export_df.empty and "Metric" in html_day_export_df.columns:
            metric_mask = html_day_export_df["Metric"].isin(["Test-In QTY", "Final Output"])
            for col in html_day_export_df.columns:
                if col != "Metric":
                    html_day_export_df.loc[metric_mask, col] = html_day_export_df.loc[metric_mask, col].apply(
                        lambda x: int(float(x))
                        if pd.notna(x) and str(x).strip() not in {"", "-", "nan"}
                        else x
                    )

        html_lrr_summary_export_df = (
            lrr_summary_export_df.copy() if lrr_summary_export_df is not None else pd.DataFrame()
        )

        if not html_lrr_summary_export_df.empty:
            if "x_label" in html_lrr_summary_export_df.columns:
                html_lrr_summary_export_df = html_lrr_summary_export_df.rename(columns={"x_label": "Date"})

            for col in ["total_lot_count", "lrr_count"]:
                if col in html_lrr_summary_export_df.columns:
                    html_lrr_summary_export_df[col] = pd.to_numeric(
                        html_lrr_summary_export_df[col], errors="coerce"
                    ).apply(lambda x: int(x) if pd.notna(x) else x)

        report_html = build_station_html_report(
            station_label=section_label,
            day_fig=day_fig,
            summary_fig=summary_fig,
            lrr_fig=lrr_fig,
            mother_lot_fig=mother_lot_fig,
            schedule_no_fig=schedule_no_fig,
            top_fail_fig=row5_fig,
            retest_fail_fig=row6_fig,
            first_pass_fig=row7_fig,
            row5_date_scope=row5_date_scope,
            row6_left_date_scope=row6_date_scope,
            row6_right_date_scope=row7_date_scope,
            daily_trend_df=html_day_export_df,
            summary_chart_df=summary_export_df,
            lrr_summary_df=html_lrr_summary_export_df,
            mother_lot_table_df=mother_lot_export_df,
            schedule_no_table_df=schedule_no_export_df,
            top_fail_table_df=row5_export_df,
            retest_fail_table_df=row6_export_df,
            first_pass_table_df=row7_export_df,
            show_tables=show_tables,
            kpi_cards_html=kpi_cards_html,
            kpi_target_labels_html=kpi_target_labels_html,
            handler_site_7day_figs=row7_7day_figs,
        )

        export_file_stub = section_label.replace(" ", "_").replace("/", "_").replace("-", "_")

        st.download_button(
            label=f"Download HTML Report - {section_label}",
            data=report_html,
            file_name=f"SIP_Yield_Dashboard_{export_file_stub}.html",
            mime="text/html",
            key=f"download_html_{scope_key}"
        )

        save_path = EXPORT_DIR / f"SIP_Yield_Dashboard_{export_file_stub}.html"
        save_path.write_text(report_html, encoding="utf-8")

        yesterday = get_previous_day()

        should_export_shared_daily = scope_has_header_data_on_date(
            device_code=device_code,
            station_value=station_value,
            target_day=yesterday
        )

        if should_export_shared_daily:
            shared_save_path = SHARED_DAILY_HTML_DIR / f"SIP_Yield_Dashboard_{export_file_stub}.html"
            ensure_folder(shared_save_path.parent)
            shared_save_path.write_text(report_html, encoding="utf-8")
            st.caption(f"Saved shared HTML to: {shared_save_path}")
        else:
            st.caption(f"Skipped shared HTML export — no data on {yesterday.strftime('%Y-%m-%d')}")

        st.caption(f"Saved HTML to: {save_path}")

        export_l2_analysis_txt_files(
            device_code=device_code,
            station_value=station_value,
            section_label=section_label,
            daily_summary_errcode_df=daily_summary_errcode_df,
            row5_rpr_errcode_df=row5_rpr_errcode_df
        )

    except Exception as e:
        st.warning(f"HTML export not available: {e}")

# =========================================================
# GENERIC RENDERERS FOR YOY / QOQ / MOM
# =========================================================
def render_period_yield_trend(
    plot_df: pd.DataFrame,
    section_label: str,
    period_label: str,
    device_code: str,
    station_value: str | None
):
    if plot_df.empty:
        st.info(f"No {period_label} yield trend data available.")
        return None

    plot_df = plot_df.copy()

    full_daily_df = get_kpi_target_source_df(device_code, station_value)
    monthly_lot_df = get_monthly_lot_target_source_df(device_code, station_value)
    target_info = get_scope_target_limits_from_back_months(monthly_lot_df)
    target_labels_html = get_kpi_target_labels_html(full_daily_df, monthly_lot_df)

    summary_html = get_period_fty_summary_html(plot_df, period_label, section_label)

    # prevent undefined / None / nan from rendering
    if summary_html is None or str(summary_html).strip().lower() in {"", "none", "nan", "undefined"}:
        summary_html = ""

    st.markdown(
        f"""
        <div style="margin:0 0 -35px 0; padding:0; line-height:1.1;">
            {summary_html}
        </div>
        """,
        unsafe_allow_html=True
    )

# Removed target label display for top-level YoY/QoQ/MoM charts
# st.markdown(target_labels_html, unsafe_allow_html=True)

    x_order = plot_df["x_label"].tolist()

    yield_df = plot_df[["1st Yield", "FTY"]].apply(pd.to_numeric, errors="coerce")

    max_yield = pd.concat([
        yield_df.stack().dropna(),
        pd.Series([95.0])
    ]).max()

    min_yield = pd.concat([
        yield_df.stack().dropna(),
        pd.Series([95.0])
    ]).min()

    auto_yield_min = max(85, round(float(min_yield) - 1.0, 2))
    auto_yield_max = max(101, round(float(max_yield) + 1.2, 2))

    if auto_yield_max <= auto_yield_min:
        auto_yield_max = auto_yield_min + 2

    max_qty = pd.to_numeric(
        pd.concat([plot_df["Test-In QTY"], plot_df["Final Output"]]),
        errors="coerce"
    ).fillna(0).max()

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["Test-In QTY"],
            name="Test-In QTY",
            marker_color=MEDIUM_BLUE,
            opacity=0.50,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in plot_df["Test-In QTY"]],
            textposition="outside",
            textfont=dict(color="black", size=12),
            cliponaxis=False,
            hovertemplate="Bucket=%{x}<br>Test-In QTY=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["Final Output"],
            name="Final Output",
            marker_color=MEDIUM_LIGHT_GREEN,
            opacity=0.45,
            text=[f"<b>{int(v):,}</b>" if pd.notna(v) else "" for v in plot_df["Final Output"]],
            textposition="outside",
            textfont=dict(color="black", size=12),
            cliponaxis=False,
            hovertemplate="Bucket=%{x}<br>Final Output=%{y:,}<extra></extra>"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["1st Yield"],
            mode="lines+markers",
            name="1st Yield",
            yaxis="y2",
            line=dict(color="#4F81BD", width=3),
            marker=dict(color="#4F81BD", size=8),
            hovertemplate="Bucket=%{x}<br>1st Yield=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["FTY"],
            mode="lines+markers",
            name="FTY",
            yaxis="y2",
            line=dict(color="#92D050", width=3),
            marker=dict(color="#92D050", size=8),
            hovertemplate="Bucket=%{x}<br>FTY=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.update_layout(
        title=None,
        height=760,
        barmode="group",
        hovermode="x unified",
        font=dict(size=12, color="black"),
        dragmode="zoom",
        xaxis=dict(
            title="Period",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Quantity",
            side="left",
            range=[0, max(10, round(float(max_qty) * 1.28, 0))]
        ),
        yaxis2=dict(
            title="Yield %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[auto_yield_min, auto_yield_max]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0
        ),
        margin=dict(l=70, r=95, t=20, b=150)
    )

    st.plotly_chart(fig, use_container_width=True)
    return fig

def render_period_fty_errcode_chart(plot_df: pd.DataFrame, section_label: str, period_label: str):
    if plot_df.empty:
        st.info(f"No {period_label} defect rate data available.")
        return None, pd.DataFrame()

    x_order = plot_df["x_label"].tolist()

    # QOQ_UNIQUE_ERRCODE_COLOR_TEST = period_label == "QoQ"
    QOQ_UNIQUE_ERRCODE_COLOR_TEST = False

    qoq_palette = [
        "#F79646", "#8064A2", "#C0504D", "#4BACC6", "#9BBB59",
        "#4F81BD", "#A64D79", "#E69138", "#674EA7", "#CC4125",
        "#45818E", "#6AA84F", "#3C78D8", "#C27BA0", "#B45F06",
        "#351C75", "#990000", "#134F5C", "#38761D", "#1155CC",
        "#741B47", "#783F04", "#20124D", "#660000", "#0C343D",
        "#274E13", "#1C4587", "#A2C4C9", "#D5A6BD", "#B6D7A8",
        "#A4C2F4", "#F9CB9C", "#D9D2E9", "#EA9999", "#B7B7B7",
    ]

    def rgba(hex_color: str, alpha: float = 0.80) -> str:
        h = hex_color.lstrip("#")
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    def build_unique_errcode_color_map(df: pd.DataFrame) -> dict:
        labels = []

        for label_col in ["top1_label", "top2_label", "top3_label", "top4_label", "top5_label"]:
            if label_col not in df.columns:
                continue

            for val in df[label_col].tolist():
                s = str(val).strip()
                if s and s not in {"-", "nan", "None"} and s not in labels:
                    labels.append(s)

        return {
            label: rgba(qoq_palette[i % len(qoq_palette)], 0.80)
            for i, label in enumerate(labels)
        }

    qoq_err_color_map = build_unique_errcode_color_map(plot_df) if QOQ_UNIQUE_ERRCODE_COLOR_TEST else {}

    stack_df = plot_df[
        ["top1_fail_pct", "top2_fail_pct", "top3_fail_pct", "top4_fail_pct", "top5_fail_pct", "other_fail_pct"]
    ].apply(pd.to_numeric, errors="coerce").fillna(0)

    max_defect_stack = stack_df.sum(axis=1).max()
    if pd.isna(max_defect_stack):
        max_defect_stack = 2

    auto_defect_max = max(1.5, round(float(max_defect_stack) * 1.25, 2))

    color_map = {
        "top1_fail_pct": "#F79646",
        "top2_fail_pct": "#8064A2",
        "top3_fail_pct": "#C0504D",
        "top4_fail_pct": "#4BACC6",
        "top5_fail_pct": "#7F7F7F",
        "other_fail_pct": "#D9D9D9",
    }

    label_map = {
        "top1_fail_pct": "Top1 errCode FTY",
        "top2_fail_pct": "Top2 errCode FTY",
        "top3_fail_pct": "Top3 errCode FTY",
        "top4_fail_pct": "Top4 errCode FTY",
        "top5_fail_pct": "Top5 errCode FTY",
        "other_fail_pct": "Other errCodes FTY",
    }

    hover_label_cols = {
        "top1_fail_pct": "top1_label",
        "top2_fail_pct": "top2_label",
        "top3_fail_pct": "top3_label",
        "top4_fail_pct": "top4_label",
        "top5_fail_pct": "top5_label",
    }

    fig = go.Figure()

    for fail_col in ["top1_fail_pct", "top2_fail_pct", "top3_fail_pct", "top4_fail_pct", "top5_fail_pct"]:
        label_col = hover_label_cols[fail_col]

        fig.add_trace(
            go.Bar(
                x=plot_df["x_label"],
                y=plot_df[fail_col],
                name=label_map[fail_col],
                marker_color=color_map[fail_col],   # fixed color per Top1–Top5
                opacity=0.85,
                customdata=plot_df[[label_col]].values,
                hovertemplate="Bucket=%{x}<br>ErrCode=%{customdata[0]}<br>Fail %=%{y:.2f}%<extra></extra>"
            )
        )

    fig.add_trace(
        go.Bar(
            x=plot_df["x_label"],
            y=plot_df["other_fail_pct"],
            name=label_map["other_fail_pct"],
            marker_color=rgba("#D9D9D9", 0.80) if QOQ_UNIQUE_ERRCODE_COLOR_TEST else color_map["other_fail_pct"],
            opacity=1.0 if QOQ_UNIQUE_ERRCODE_COLOR_TEST else 0.85,
            hovertemplate="Bucket=%{x}<br>Other errCodes Fail %=%{y:.2f}%<extra></extra>"
        )
    )

    total_fail_pct = stack_df.sum(axis=1).round(2)

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["FPY"],
            mode="lines+markers+text",
            name="FPY",
            yaxis="y2",
            line=dict(color="#4F81BD", width=3),
            marker=dict(color="#4F81BD", size=8),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["FPY"]],
            textposition="top center",
            textfont=dict(color="black", size=12),
            cliponaxis=False,
            hovertemplate="Bucket=%{x}<br>FPY=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=plot_df["FTY"],
            mode="lines+markers+text",
            name="FTY",
            yaxis="y2",
            line=dict(color="#92D050", width=4),
            marker=dict(color="#92D050", size=9),
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in plot_df["FTY"]],
            textposition="top center",
            textfont=dict(color="black", size=12),
            cliponaxis=False,
            hovertemplate="Bucket=%{x}<br>FTY=%{y:.2f}%<extra></extra>",
            connectgaps=True
        )
    )

    fig.add_trace(
        go.Scatter(
            x=plot_df["x_label"],
            y=total_fail_pct,
            mode="text",
            name="Total Defect %",
            text=[f"{v:.2f}%" if pd.notna(v) else "" for v in total_fail_pct],
            textposition="top center",
            textfont=dict(color="black", size=12),
            cliponaxis=False,
            hoverinfo="skip",
            showlegend=False,
            connectgaps=True
        )
    )

    fig.update_layout(
        title=f"{section_label} Top 5 Defect Rate Distribution - {period_label}",
        height=900,
        barmode="stack",
        hovermode="x unified",
        font=dict(size=12, color="black"),
        dragmode="zoom",
        xaxis=dict(
            title="Period",
            type="category",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45
        ),
        yaxis=dict(
            title="Defect Rate %",
            range=[0, auto_defect_max]
        ),
        yaxis2=dict(
            title="Yield %",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[85, 101]
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        margin=dict(l=70, r=95, t=130, b=150)
    )

    st.plotly_chart(fig, use_container_width=True)

    export_df = plot_df[[
        "x_label",
        "FTY",
        "top1_label", "top1_fail_pct",
        "top2_label", "top2_fail_pct",
        "top3_label", "top3_fail_pct",
        "top4_label", "top4_fail_pct",
        "top5_label", "top5_fail_pct",
        "other_fail_pct"
    ]].copy()

    export_df = export_df.rename(columns={"x_label": "Date"})

    display_df = combine_pct_and_label_for_display(
        export_df,
        label_pct_pairs=[
            ("top1_label", "top1_fail_pct"),
            ("top2_label", "top2_fail_pct"),
            ("top3_label", "top3_fail_pct"),
            ("top4_label", "top4_fail_pct"),
            ("top5_label", "top5_fail_pct"),
        ],
        other_pct_col="other_fail_pct",
        other_label="Other errCodes"
    )

    display_df = display_df.rename(columns={
        "FTY": "Overall_FTY",
        "top1_label": "top1_errCode",
        "top2_label": "top2_errCode",
        "top3_label": "top3_errCode",
        "top4_label": "top4_errCode",
        "top5_label": "top5_errCode",
        "other_fail_pct": "other_errCodes"
    })

    display_df = transpose_metric_table(display_df, date_col="Date")

    display_df = order_metric_rows(
        display_df,
        ["Overall_FTY", "top1_errCode", "top2_errCode", "top3_errCode", "top4_errCode", "top5_errCode", "other_errCodes"]
    )

    display_df = format_pct_rows_in_transposed_table(
        display_df,
        ["Overall_FTY"],
        decimals=2
    )

    st.dataframe(display_df, use_container_width=True, height=320)

    return fig, display_df

# =========================================================
# MAIN
# =========================================================
def main():
    st.title("SIP Yield Dashboard")
    st.caption("DuckDB + Streamlit for SIP text-summary transformed data")

    if not DB_PATH.exists():
        st.error(f"DuckDB file not found: {DB_PATH}")
        st.stop()

    # -----------------------------------------------------
    # Auto-export cleanup
    # Clear shared HTML folders once per app run
    # -----------------------------------------------------
    if AUTO_EXPORT_MODE:
        cleanup_folder_contents(SHARED_DAILY_HTML_DIR, patterns=["*.html"])
        cleanup_folder_contents(SHARED_TOP_LEVEL_HTML_DIR, patterns=["*.html"])

    # Existing 4W device tabs (keep untouched)
    scopes = get_available_dashboard_scopes()

    if not scopes:
        st.warning("No device data found in the last 28 days.")
        st.stop()

    # New ranked scopes for YoY / QoQ / MoM
    ranked_period_scopes = get_ranked_period_scopes()

    # -----------------------------------------------------
    # Top-level tabs:
    #   1. YoY
    #   2. QoQ
    #   3. MoM
    #   4. Existing device tabs (4W current logic)
    # -----------------------------------------------------
    top_tab_labels = ["YoY", "QoQ", "MoM"] + [scope["tab_label"] for scope in scopes]
    top_tabs = st.tabs(top_tab_labels)

    # -------------------------
    # YoY tab
    # -------------------------
    with top_tabs[0]:
        st.header("YoY")
        rendered_yoy_sections = []

        if not ranked_period_scopes:
            st.info("No ranked device scopes available for YoY.")
        else:
            for scope in ranked_period_scopes:
                result = render_yoy_scope_section(
                    device_code=scope["device_code"],
                    station_value=scope["station"],
                    section_label=scope["section_label"]
                )
                rendered_yoy_sections.append(result)
                st.divider()

            yoy_report_html = build_period_tab_html_report("YoY", rendered_yoy_sections)

            ensure_folder(SHARED_TOP_LEVEL_HTML_DIR)
            yoy_shared_path = SHARED_TOP_LEVEL_HTML_DIR / "SIP_YoY_Dashboard.html"
            yoy_shared_path.write_text(yoy_report_html, encoding="utf-8")

            if AUTO_EXPORT_MODE:
                ensure_folder(SHARED_TOP_LEVEL_HTML_DIR)
                yoy_shared_path = SHARED_TOP_LEVEL_HTML_DIR / "SIP_YoY_Dashboard.html"
                yoy_shared_path.write_text(yoy_report_html, encoding="utf-8")
                st.caption(f"Saved shared HTML to: {yoy_shared_path}")

            st.download_button(
                label="Download YoY HTML Report",
                data=yoy_report_html,
                file_name="SIP_YoY_Dashboard.html",
                mime="text/html",
                key="download_yoy_full_tab_html"
            )

    # -------------------------
    # QoQ tab
    # -------------------------
    with top_tabs[1]:
        st.header("QoQ")
        rendered_qoq_sections = []

        if not ranked_period_scopes:
            st.info("No ranked device scopes available for QoQ.")
        else:
            for scope in ranked_period_scopes:
                result = render_qoq_scope_section(
                    device_code=scope["device_code"],
                    station_value=scope["station"],
                    section_label=scope["section_label"]
                )
                rendered_qoq_sections.append(result)
                st.divider()

            qoq_report_html = build_period_tab_html_report("QoQ", rendered_qoq_sections)

            ensure_folder(SHARED_TOP_LEVEL_HTML_DIR)
            qoq_shared_path = SHARED_TOP_LEVEL_HTML_DIR / "SIP_QoQ_Dashboard.html"
            qoq_shared_path.write_text(qoq_report_html, encoding="utf-8")

            if AUTO_EXPORT_MODE:
                ensure_folder(SHARED_TOP_LEVEL_HTML_DIR)
                qoq_shared_path = SHARED_TOP_LEVEL_HTML_DIR / "SIP_QoQ_Dashboard.html"
                qoq_shared_path.write_text(qoq_report_html, encoding="utf-8")
                st.caption(f"Saved shared HTML to: {qoq_shared_path}")

            st.download_button(
                label="Download QoQ HTML Report",
                data=qoq_report_html,
                file_name="SIP_QoQ_Dashboard.html",
                mime="text/html",
                key="download_qoq_full_tab_html"
            )

    # -------------------------
    # MoM tab
    # -------------------------
    with top_tabs[2]:
        st.header("MoM")
        rendered_mom_sections = []

        if not ranked_period_scopes:
            st.info("No ranked device scopes available for MoM.")
        else:
            for scope in ranked_period_scopes:
                result = render_mom_scope_section(
                    device_code=scope["device_code"],
                    station_value=scope["station"],
                    section_label=scope["section_label"]
                )
                rendered_mom_sections.append(result)
                st.divider()

            mom_report_html = build_period_tab_html_report("MoM", rendered_mom_sections)

            ensure_folder(SHARED_TOP_LEVEL_HTML_DIR)
            mom_shared_path = SHARED_TOP_LEVEL_HTML_DIR / "SIP_MoM_Dashboard.html"
            mom_shared_path.write_text(mom_report_html, encoding="utf-8")

            if AUTO_EXPORT_MODE:
                ensure_folder(SHARED_TOP_LEVEL_HTML_DIR)
                mom_shared_path = SHARED_TOP_LEVEL_HTML_DIR / "SIP_MoM_Dashboard.html"
                mom_shared_path.write_text(mom_report_html, encoding="utf-8")
                st.caption(f"Saved shared HTML to: {mom_shared_path}")

            st.download_button(
                label="Download MoM HTML Report",
                data=mom_report_html,
                file_name="SIP_MoM_Dashboard.html",
                mime="text/html",
                key="download_mom_full_tab_html"
            )

    # -------------------------
    # Existing device tabs (4W logic untouched)
    # -------------------------
    for tab, scope in zip(top_tabs[3:], scopes):
        with tab:
            render_scope_section(
                device_code=scope["device_code"],
                station_value=scope["station"],
                section_label=scope["section_label"]
            )

if __name__ == "__main__":
    main()


# In[ ]:




