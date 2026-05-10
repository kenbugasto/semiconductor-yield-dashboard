# SIP Yield Dashboard

> **Note:** All data, device names, handler names, stations, soft bins, errCodes, and identifiers shown in this repository have been fully anonymized for public portfolio usage. No proprietary manufacturing or customer-sensitive data is included.

Production-style semiconductor manufacturing analytics dashboard built using Streamlit, DuckDB, Python, SQL, and Plotly.

---

# Overview

This project is a production-style semiconductor manufacturing analytics dashboard built using:

- Python
- Streamlit
- DuckDB
- Pandas
- Plotly

The dashboard automates:
- raw test file ingestion
- ETL transformation
- KPI aggregation
- yield monitoring
- defect pareto generation
- retest analytics
- handler/site analysis
- automated HTML report exports

The analytics workflows were designed around semiconductor final-test manufacturing operations, including lot-level yield tracking, retest analysis, defect monitoring, and equipment-level performance investigation.

---

## Data Sources

The dashboard pipeline processes manufacturing production data originating from:
- raw `.txt` production logs
- `.log` equipment output files

The ETL pipeline standardizes and transforms these semi-structured files into analytics-ready DuckDB tables for downstream KPI monitoring and reporting.

---

# Core Features

## ETL / Data Engineering
- Automated raw text/CSV ingestion
- SQL-based analytical transformations
- DuckDB-based analytical warehouse
- Window-function based deduplication pipelines
- Incremental loader pipeline
- Automated HTML report generation
- Shared export automation
- Config-driven deployment architecture

## Manufacturing Analytics
- 4-week yield monitoring
- First Pass Yield (FPY)
- Final Test Yield (FTY)
- Low Running Rate (LRR)
- Retest Pass Rate (RPR)
- Top defect pareto analysis
- Handler correlation analysis
- Handler-site root cause analysis

## Trend Analytics
- YoY trend monitoring
- QoQ trend monitoring
- MoM trend monitoring

---

# Technology Stack

| Category | Tools |
|---|---|
| Language | Python |
| Query Language | SQL |
| Dashboard | Streamlit |
| Database | DuckDB |
| Data Processing | Pandas |
| ETL | Python + SQL |
| Visualization | Plotly |
| Automation | Windows Task Scheduler |
| Reporting | HTML Export |

---

# Dashboard Screenshots

---

# KPI Cards

![KPI Cards](screenshots/row01_4week_KPI%20cards.png)

Production KPI summary cards for:
- FPY(First Pass Yield)
- FTY(Final Test Yield)
- RPR(Retest Pass Rate)
- LRR(Lot Rejection Rate)
- input/output quantity tracking

---

# 4-Week Yield Trend

![4W Yield](screenshots/row02_4week_yield_trend.png)

4-week rolling yield monitoring with:
- test-in quantity
- output quantity
- first yield
- final yield
- dynamic target tracking

---

# Top 5 Defect Distribution

![Top5 Defect](screenshots/row03_top5_defect_distribution.png)

Defect pareto analysis with:
- top defect contributors
- stacked defect rate monitoring
- FPY / FTY overlay trend visualization

---

# Mother Lot Yield Trend

![Mother Lot Yield](screenshots/row09_mother_lot_yield_trend.png)

Semiconductor mother-lot monitoring analytics used for:
- split lot visibility
- upstream process excursion tracking
- related lot yield comparison
- manufacturing root-cause investigation

The dashboard supports rolling yield monitoring and defect visibility at mother-lot aggregation level.

---

# Per Lot Yield Trend

![Per Lot Yield](screenshots/row10_per_lot_yield_trend.png)

Lot-level yield analytics supporting:
- production lot monitoring
- abnormal yield detection
- retest investigation
- engineering containment workflows
- detailed lot-by-lot yield comparison

Provides detailed visibility into individual production schedule performance.

# LRR Trend Monitoring

![LRR Trend](screenshots/row04_lrr_trend.png)

Lot Rejection Rate monitoring dashboard including:
- LRR %
- LRR lot counts
- historical trend tracking
- automatic trigger thresholds

---

# LRR Summary Table

![LRR Summary](screenshots/row05_lrr_summary_table.png)

Detailed LRR summary analytics with:
- affected lots
- mother lot visibility
- rolling 4-week aggregation

---

# Retest Pass Rate errCode Distribution

![RPR errCode](screenshots/row06_rpr_errcode_distribution.png)

Retest recovery analytics showing:
- high RPR errCodes
- recovery contribution distribution
- defect recovery monitoring

---

# Handler vs RPR Analysis

![Handler RPR](screenshots/row07_handler_rpr.png)

Equipment-level analytics for:
- handler contribution analysis
- retest recovery comparison
- top errCode contributors per handler

---

# Handler-Site vs RPR Analysis

![Handler Site RPR](screenshots/row08_handler-site_rpr_top_only.png)

Root-cause isolation analytics using:
- handler-site combinations
- top recovery contributors
- high-risk site detection

---

# YoY Yield Trend

![YoY Yield](screenshots/top_lvl_chart_yoy_yield_trend.png)

Year-over-Year yield comparison analytics.

---

# YoY Top 5 Defect Rate

![YoY Defect](screenshots/top_lvl_chart_yoy_top5_defect_rate.png)

Year-over-Year defect pareto monitoring.

---

# QoQ Yield Trend

![QoQ Yield](screenshots/top_lvl_chart_qoq_yield_trend.png)

Quarter-over-Quarter yield trend analytics.

---

# QoQ Top 5 Defect Rate

![QoQ Defect](screenshots/top_lvl_chart_qoq_top5_defect_rate.png)

Quarter-over-Quarter defect distribution monitoring.

---

# MoM Yield Trend

![MoM Yield](screenshots/top_lvl_chart_mom_yield_trend.png)

Month-over-Month yield analytics.

---

# MoM Top 5 Defect Rate

![MoM Defect](screenshots/top_lvl_chart_mom_top5_defect_rate.png)

Month-over-Month defect trend monitoring.

# Architecture & Technology Decisions

## Why DuckDB

DuckDB was selected as the primary analytics database because it provides:

- lightweight deployment
- fast analytical query performance
- minimal infrastructure overhead
- embedded SQL analytics capability
- easy portability within restricted enterprise environments

The project environment had limitations on deploying and managing heavier database platforms such as SQL Server or PostgreSQL, so DuckDB provided a highly effective local analytics solution for manufacturing KPI workloads.
---

## Why Streamlit

Streamlit was selected because it enabled rapid development of interactive analytics dashboards without requiring enterprise BI licensing or additional infrastructure.

Benefits included:

- lightweight deployment
- Python-native integration
- rapid visualization development
- standalone HTML export generation
- simplified maintenance for internal reporting workflows

This allowed the dashboard to function as a practical alternative for internal manufacturing analytics and reporting under constrained tooling environments.

---

# Repository Structure

```text
SIP_Dashboard_GitHub/
│
├── screenshots/
├── src/
├── docs/
├── README.md
