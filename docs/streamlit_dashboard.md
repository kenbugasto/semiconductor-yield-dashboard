# Streamlit Dashboard Overview

## Overview

This project contains a production-style Streamlit dashboard designed for semiconductor final test yield monitoring and manufacturing analytics.

The dashboard reads transformed summary data from DuckDB and provides interactive KPI monitoring, yield trend analysis, retest analysis, defect pareto visualization, and automated HTML export generation.

All uploaded GitHub data and screenshots are anonymized for portfolio and demonstration purposes.

---

# Main Features

## 1. KPI Monitoring
- FTY (Final Test Yield)
- FPY (First Pass Yield)
- Retest Pass Rate (RPR)
- Low Running Rate (LRR)
- Input / Output quantities

---

## 2. Yield Trend Analysis

### 4-Week Yield Trend
- Test-In quantity
- Final output quantity
- 1st Yield
- FTY trend

### Top-Level Trends
- YoY (Year-over-Year)
- QoQ (Quarter-over-Quarter)
- MoM (Month-over-Month)

---

## 3. Defect Pareto Monitoring
- Top 5 errCode distribution
- Defect rate stacking
- Dynamic yield overlays
- Weekly defect monitoring

---

## 4. Retest Analytics

### Handler vs RPR
- Top retest contributors
- Handler performance analysis

### Handler-Site vs RPR
- Top handler-site combinations
- Time-series monitoring
- High contributor tracking

---

## 5. LRR Monitoring
- LRR trend chart
- LRR summary tables
- Failed lot tracking
- Lot-level monitoring

---

# Dashboard Technical Features

## Streamlit
Interactive frontend framework for visualization and reporting.

## DuckDB
Local analytics database used for high-performance SQL aggregation.

## Plotly
Interactive chart rendering and HTML export support.

## Automated HTML Export
Dashboard sections can be exported into standalone HTML reports for:
- management review
- offline sharing
- AI-assisted analytics workflows

---

# Key Engineering Concepts Used

- SQL window functions
- CTE-based aggregation
- Dynamic filtering
- Yield KPI calculations
- Retest analysis
- Time-series analytics
- Data anonymization
- Automated report generation
- Modular Streamlit architecture

## Statistical Monitoring Logic

The dashboard also incorporates basic statistical monitoring concepts for manufacturing analytics workflows, including:

- Interquartile Range (IQR)-based outlier detection
- historical yield target calculations
- rolling KPI trend analysis
- 3-sigma style yield monitoring concepts
- defect contribution ranking

These calculations were used to support yield monitoring, retest analysis, and abnormal production trend identification.

---

# Notes

- Configuration paths are externally controlled through `.ini` and batch launcher files.
- Sensitive manufacturing information has been anonymized before publication.
- The dashboard supports multiple device and station scopes dynamically.
