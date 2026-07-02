# 📊 SIP Yield Dashboard

> **Note:** All data, device names, handler names, stations, soft bins, errCodes, and identifiers shown in this repository have been fully anonymized for public portfolio usage. No proprietary manufacturing or customer-sensitive data is included.

Production-style semiconductor manufacturing analytics dashboard for Silicon on Package(SiP) devices built using **Python**, **DuckDB**, **SQL**, **Streamlit**, and **Plotly**.

---

# 🔎 Overview

This project is an end-to-end semiconductor manufacturing analytics solution that automates the transformation of raw production test logs into engineering dashboards and manufacturing KPI reports.

The solution demonstrates a complete local analytics pipeline built using modern data engineering principles, including:

* Automated raw file ingestion
* ETL transformation
* Data validation and standardization
* SQL-based analytical modeling
* KPI aggregation
* Interactive dashboard visualization
* Automated HTML report generation

The analytics workflows were designed around semiconductor final-test manufacturing operations, supporting engineering investigations such as yield monitoring, defect analysis, retest recovery, and equipment performance evaluation.

---

# 🚀 Interactive Dashboard Demo

This project generates standalone interactive HTML dashboards that enable engineers and stakeholders to explore semiconductor manufacturing KPIs without requiring Python, a database connection, or BI software.

The sample dashboards below are generated using anonymized data and demonstrate both operational and executive-level reporting.

## 📊 Device Dashboard

Daily manufacturing dashboard for a single semiconductor device featuring:

- Yield and Retest Pass Rate (RPR) trends
- Interactive defect Pareto analysis
- Expandable data tables

**🌐 Launch Interactive Dashboard Demo:**  
[SiP_Yield_Dashboard_DV029_device.html](https://kenbugasto.github.io/semiconductor-yield-dashboard/demo/SIP_Yield_Dashboard_DV029_device.html)

---

## Executive Performance Dashboard

Management dashboard consolidating multiple semiconductor devices into a single executive view featuring:

- Year-over-Year (YoY) analysis
- Quarter-over-Quarter (QoQ) analysis
- Month-over-Month (MoM) analysis
- Cross-device KPI trend visualization

**🌐 Launch Interactive Dashboard Demo:**  
- [SIP_YoY_Dashboard_Demo.html](https://kenbugasto.github.io/semiconductor-yield-dashboard/demo/SIP_YoY_Dashboard_Demo.html)
- [SIP_QoQ_Dashboard_Demo.html](https://kenbugasto.github.io/semiconductor-yield-dashboard/demo/SIP_QoQ_Dashboard_Demo.html)
- [SIP_MoM_Dashboard_Demo.html](https://kenbugasto.github.io/semiconductor-yield-dashboard/demo/SIP_MoM_Dashboard_Demo.html)

> Note: These standalone HTML reports can be viewed directly in a web browser and retain Plotly's interactive hover tooltips, zooming, and panning capabilities.

---

# 📈 Business Impact

## Quantified Improvements

| Category | Legacy Workflow | Modernized Platform |
|----------|-----------------|---------------------|
| Reporting Applications | **60+** Excel VBA applications | **1** Python ETL Loader + **1** Dashboard |
| Reporting Infrastructure | **6 Reporting PCs** | **1 Reporting PC** |
| Daily Processing | Independent VBA execution | Centralized ETL pipeline |
| Processing Time | ~1 hour (6 PCs) | 45–75 minutes (1 PC) |
| Dashboard Reporting | Manual PowerPoint consolidation | Interactive Streamlit dashboards + HTML reports |
| Historical Traceability | Multiple Excel files | Centralized DuckDB analytics database |
| Late Retest Handling | Manual updates | Automatic one-month rolling backfill |
| KPI Target Calculation | Manual maintenance | Automated IQR-filtered 3σ calculation |

---

This project modernizes a legacy semiconductor manufacturing reporting workflow by consolidating **60+ device-specific Excel VBA applications** into a centralized **Python-based ETL and analytics platform**.

The solution improves maintainability, scalability, and engineering productivity by centralizing manufacturing data into a queryable analytics database while automating KPI reporting, historical traceability, statistical target generation, and engineering dashboards.

---

# 🥉🥈🥇 ETL Flow -  Medallion Architecture

Although implemented locally using Python and DuckDB rather than Databricks Delta Lake, this project follows the same Medallion Architecture principles by separating raw ingestion, standardized transformations, and business-ready analytics.

![Medallion Architecture](screenshots/medallion_architecture.png)

## Bronze Layer

The Bronze layer is responsible for ingesting raw manufacturing production files while preserving their original structure and metadata.

Responsibilities include:

* Raw file ingestion
* File hash generation
* Audit logging
* Source metadata preservation
* Incremental load tracking

## Silver Layer

The Silver layer transforms semi-structured manufacturing logs into standardized analytical tables.

Primary tables:

* `file_header`
* `detail_2d_list`

Responsibilities include:

* Schema standardization
* Timestamp normalization
* Data validation
* Duplicate removal
* Manufacturing business rule enforcement

## Gold Layer

The Gold layer prepares business-ready manufacturing analytics used by engineering dashboards and automated reports.

Examples include:

* First Pass Yield (FPY)
* Final Test Yield (FTY)
* Lot Rejection Rate (LRR)
* Retest Pass Rate (RPR)
* Top Defect Pareto
* Handler Analytics
* Year-over-Year (YoY), Quarter-over-Quarter (QoQ), and Month-over-Month (MoM) trend analysis

---

# 🧩 Data Model

The ETL pipeline separates lot-level manufacturing metadata from unit-level production records. This normalized design minimizes duplicated information while supporting efficient KPI reporting, engineering investigations, and complete traceability back to the original production file.

![Data Model](screenshots/data_model.png)

## Modeling Decisions

The data model intentionally separates lot-level metadata from unit-level manufacturing test records.

This normalization provides several advantages:

* Reduces duplicated information
* Supports efficient lot-level KPI aggregation
* Enables detailed unit-level defect investigation
* Maintains complete traceability through `file_hash`
* Simplifies future expansion for additional manufacturing analytics

---

# 🗂️ Data Sources

The dashboard processes manufacturing production data originating from:

* Raw `.txt` production logs
* `.log` equipment output files

The ETL pipeline transforms these semi-structured manufacturing files into analytics-ready DuckDB tables for downstream reporting and dashboard visualization.

---

# 🛠️ Technology Stack

| Category        | Technology             |
| --------------- | ---------------------- |
| Language        | Python                 |
| Query Language  | SQL                    |
| Database        | DuckDB                 |
| Data Processing | Pandas                 |
| Dashboard       | Streamlit              |
| Visualization   | Plotly                 |
| ETL             | Python + SQL           |
| Scheduling      | Windows Task Scheduler |
| Reporting       | HTML Export            |

---

# 🏗️ Architecture & Technology Decisions

## Why DuckDB

DuckDB was selected as the analytical database because it provides:

* Lightweight deployment
* Fast analytical query performance
* Embedded SQL execution
* Minimal infrastructure requirements
* Easy portability within restricted enterprise environments

The project environment did not permit deploying heavier database platforms such as SQL Server or PostgreSQL. DuckDB provided an excellent balance between analytical performance and operational simplicity for manufacturing KPI workloads.

---

## Why Streamlit

Streamlit enabled rapid development of interactive engineering dashboards without requiring enterprise BI licensing or additional infrastructure.

Benefits include:

* Python-native development
* Rapid dashboard creation
* Interactive engineering visualization
* Standalone HTML report generation
* Low maintenance overhead

This allowed the dashboard to function as a lightweight internal manufacturing analytics platform under constrained tooling environments.

---

## 🧱 Production SQL Design

The dashboard uses SQL as the main analytical modeling layer between the cleaned DuckDB tables and the Streamlit / Plotly dashboard outputs.

One key SQL design pattern is the use of CTEs, joins, aggregations, and window functions to generate business-ready yield metrics from lot-level and unit-level manufacturing data.

### Example: Lot-Level Yield Source for Dynamic KPI Targets

```sql
WITH scoped_header AS (
    SELECT *
    FROM file_header
    WHERE device_code = '<selected_device>'
      AND station = '<selected_station>'
      AND CAST(end_time AS DATE) >= DATE '2023-01-01'
),
latest_header_per_lot_day AS (
    SELECT *
    FROM (
        SELECT
            *,
            CAST(end_time AS DATE) AS test_date,
            ROW_NUMBER() OVER (
                PARTITION BY
                    device_code,
                    station,
                    schedule_no,
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
    test_date,
    schedule_no,
    SUM(COALESCE(input_quantity, 0)) AS input_quantity,
    SUM(COALESCE(first_pass_qty, 0)) AS first_pass_qty,
    SUM(COALESCE(final_pass_qty, 0)) AS final_pass_qty
FROM latest_header_per_lot_day
GROUP BY test_date, schedule_no
ORDER BY test_date, schedule_no;
```

### Why This Matters

This SQL block prepares the lot-level dataset used for dynamic FPY and FTY target calculation. The logic first scopes the data to the selected device and station, then applies a window function to keep only the latest trusted production record per lot and production day.

This prevents duplicate uploads or regenerated reports from inflating production quantity, yield, and defect metrics.

### Engineering Highlights

* Uses CTEs to separate filtering, deduplication, and aggregation steps.
* Uses `ROW_NUMBER()` to select one trusted record per lot/day cohort.
* Uses deterministic ordering through `end_time`, `source_modified_time`, and `file_hash`.
* Produces a clean Gold-layer dataset for KPI target calculation.
* Supports dashboard metrics such as FPY, FTY, LRR, RPR, and long-term trend analysis.

### IQR-Based 3-Sigma Target Logic

After SQL prepares the lot-level yield source, Python applies an IQR filter before calculating 3-sigma lower control limits.

This avoids allowing abnormal excursion lots to distort KPI targets.

```python
def calc_iqr_filtered_3sig_lcl(values: pd.Series):
    vals = pd.to_numeric(values, errors="coerce").dropna().astype(float)

    if vals.empty:
        return None, None, None, 0

    q1 = vals.quantile(0.25)
    q3 = vals.quantile(0.75)
    iqr = q3 - q1

    lower_limit = q1 - (1.5 * iqr)
    upper_limit = q3 + (1.5 * iqr)

    kept = vals[(vals >= lower_limit) & (vals <= upper_limit)].copy()

    if kept.empty:
        kept = vals.copy()

    avg_val = float(kept.mean())
    sigma_val = float(kept.std(ddof=0))
    raw_lcl = round(avg_val - (3 * sigma_val), 2)

    return round(avg_val, 2), round(sigma_val, 2), raw_lcl, int(len(kept))
```

The final target calculation uses the latest available back-month data, removes statistical outliers, calculates average minus three sigma, and applies safe fallback defaults for new devices with limited history.

This combines analytics engineering and manufacturing process-control logic into a repeatable KPI target engine.

---

## 🐍 Production Python ETL Design

The Python loader implements a defensive ETL pipeline for daily semiconductor manufacturing reporting. It does not assume that every source file is valid, complete, current, or in scope.

The loader performs file discovery, one-month auto backfill, parsing, validation, incremental loading, auditing, deduplication, metric refresh, and secured export generation.

### Example: One-Month Auto Backfill and Defensive File Discovery

```python
def get_1month_cutoff() -> datetime:
    return (
        pd.Timestamp.today().normalize()
        - pd.DateOffset(months=1)
    ).to_pydatetime()


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
```

### Why This Matters

The previous daily reporting workflow was vulnerable to missing, late, or regenerated files. A strict previous-day-only process could miss files that arrived late or were re-exported after the original report run.

The one-month backfill logic solves this by scanning a rolling one-month window instead of relying only on a single daily file set. This makes the pipeline more resilient to delayed uploads, reruns, and manufacturing system timing issues.

### Defensive ETL Pattern

```python
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
```

The loader combines pandas-based date handling, regular expressions, file metadata fallback, and hash-based incremental loading. This ensures that only relevant and changed files are processed.

### Engineering Highlights

* One-month rolling backfill improves reliability compared with strict daily-only processing.
* File hash auditing prevents unchanged files from being processed repeatedly.
* Customer and station validation prevents non-scoped data from entering the analytical database.
* Failed files are logged without hiding the root cause.
* Delete-and-reload by `file_hash` makes reruns deterministic.
* Post-load deduplication protects KPI calculations from duplicate uploaded reports.
* Final security checks prevent non-scoped customer, station, or device records from being exported.

This design makes the pipeline repeatable, auditable, and suitable for automated daily manufacturing analytics.

---

# 🛣️ Project Roadmap

## Current Features

* Automated TXT / LOG ingestion pipeline
* Semiconductor yield analytics dashboard
* Manufacturing KPI reporting
* Retest and defect monitoring
* Automated HTML report generation
* Mother-lot and lot-level analytics
* YoY / QoQ / MoM manufacturing trend analysis

---

## In Progress

### 🔬 Phase 2 Manufacturing Analytics Expansion

The next phase extends the platform from manufacturing yield analytics into test parameter and statistical process analysis, enabling engineers to identify process shifts before they impact production yield.

Planned capabilities include:

* Automated CSV test parameter data ingestion
* Statistical process capability reporting (Cp, Cpk, Cpu, Cpl)
* Parametric trend monitoring across production lots
* Control chart visualization for key test parameters
* Outlier and excursion detection
* Automated engineering summary reports
* Interactive dashboard for parameter-level analysis

---

# 🎯 Key Engineering Concepts Demonstrated

This project demonstrates practical implementation of several data engineering concepts commonly used in modern analytics platforms:

### Data Engineering

* Incremental ETL pipelines
* Defensive ETL design
* Configuration-driven architecture
* Data validation
* Audit logging
* Incremental loading
* Data normalization

### SQL Engineering

* Common Table Expressions (CTEs)
* Window Functions
* Analytical aggregations
* Manufacturing KPI calculations
* Data deduplication
* Multi-stage transformations

### Analytics Engineering

* Medallion Architecture
* Layered data modeling
* Business-ready analytical datasets
* Manufacturing KPI reporting
* Time-series trend analysis

### Software Engineering

* Modular Python architecture
* Configuration management
* Automated reporting
* Reusable helper functions
* Separation of concerns

---

## 👤 Author

This repository was developed as a portfolio project demonstrating production-oriented data engineering techniques applied to semiconductor manufacturing analytics.

The implementation emphasizes practical ETL design, SQL engineering, manufacturing KPI modeling, defensive data processing, and lightweight analytics architecture using Python, DuckDB, SQL, Streamlit, and Plotly.

