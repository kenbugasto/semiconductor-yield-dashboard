# Loader ETL Pipeline

## Objective

The loader pipeline automates ingestion and transformation
of semiconductor final-test production data into analytics-ready datasets.

---

## Responsibilities

The ETL process performs:

- raw TXT/CSV ingestion
- schema normalization
- data validation
- timestamp standardization
- deduplication
- defect mapping
- incremental loading
- DuckDB table loading
- logging and export automation

---

## Data Flow

Raw Production Logs
→ Python ETL
→ Data Cleaning
→ Deduplication
→ DuckDB Tables
→ Streamlit Dashboard

---

## Key Engineering Challenges Solved

### Duplicate Upload Handling

Implemented ROW_NUMBER() partitioning logic to keep only latest valid uploads.

### Corrupted CSV Rows

Implemented defensive parsing and bad-line handling.

### Incremental Backfill Logic

Designed loader to support:
- historical backfill
- daily incremental loads
- automated reruns

---

## Technologies

- Python
- Pandas
- DuckDB
- SQL