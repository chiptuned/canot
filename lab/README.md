# Test bench

A deliberately complex data warehouse, to validate `canot scan` and `canot drill` without a customer.

- `bigquery_lab.py PROJECT`: 5 datasets (raw, staging, mart, ML, external) in a personal project, free tier. Partitioned and clustered tables, STRUCT/ARRAY/JSON/GEOGRAPHY/NUMERIC/BIGNUMERIC, views, a materialized view, a snapshot, a clone, a row access policy, an external table, SQL and JavaScript routines, a BigQuery ML model, a labeled workload.
- `snowflake_lab.py ACCOUNT`: a CANOT_LAB database in a trial account. VARIANT/OBJECT/ARRAY/GEOGRAPHY/TIMESTAMP_TZ, clustering, a secure view, a materialized view, a dynamic table, SQL/JavaScript/Python routines, a stream, tasks, an external stage and integration (dummy), masking and row access policies, tags, roles, an outbound share, a tagged workload. Each object is created separately: anything that requires a higher edition is reported, then skipped.
- `dbt/`: a dbt project (staging, models, tests) that runs on both platforms and produces realistic traffic, including the JSON comments dbt adds to its queries.

No secrets are stored here: authentication goes through Google application default credentials and a local Snowflake key.
