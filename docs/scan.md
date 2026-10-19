# `canot scan`: what is read, with which permissions

The scan produces `scan.html` (to share), `scan.json` and CSV files (`flags.csv`, `families.csv`, `reads.csv`, `tables.csv`). It describes your platform; it recommends neither leaving nor staying.

## Guarantees

- **Read-only.** (dbt project: no queries, only files are read, see [dbt](#dbt).) Every query goes through a guard before it is sent: only `SELECT`, `SHOW`, `DESCRIBE` and `ALTER SESSION SET` are allowed. Everything else is rejected without sending anything. `SYSTEM$` functions are rejected too.
- **Never the content of your tables.** The scan reads metadata and execution history. It requests no read permission on the data.
- **Raw SQL is not written.** Literals are masked (`WHERE email = ?`), and queries that contain a password or a secret are counted, then discarded. Raw text is written only with `--include-sql-text`, to `scan-sql-raw.json` (permissions 0600), and the report says so.
- **Pseudonymized identities.** Query users and roles are replaced with a pseudonym (HMAC, with a salt drawn for each scan and never written). Email addresses, login names and IP addresses are never read. Storage paths are reduced to the bucket name.
- **No read silently lost.** Every read is recorded as "read" or "not read" with its reason. A value that was not read stays empty, never 0.

## Levels (`--level`, 3 by default)

| Level | Content | Additional permissions |
|---|---|---|
| 1 | Inventory, column types, volumes, billed history, consumption, objects (views, routines, tasks, stages, shares…), dependencies | metadata and consumption |
| 2 | + normalized query shapes and their replayability, reads and writes per table, policies | access to query history |
| 3 | + owner roles, client tools, user types, reserved capacity | grants and sessions |

What your permissions do not allow is recorded as "not read" in the report (finding F12): the scan reads what it has access to and states what it did not see.

## Snowflake

`canot snowflake-setup --generate-key --base MY_DATABASE` writes the script for an administrator to review and run. It creates a role, an XS warehouse that suspends after 60 s and a credit cap, and grants Snowflake's database roles:

| Database role | Level | Used for |
|---|---|---|
| `SNOWFLAKE.OBJECT_VIEWER` | 1 | tables, columns, views, routines, tasks, stages, dependencies |
| `SNOWFLAKE.USAGE_VIEWER` | 1 | volumes, credits, history, loads |
| `SNOWFLAKE.GOVERNANCE_VIEWER` | 2 | query history, reads per table, policies, tags |
| `SNOWFLAKE.SECURITY_VIEWER` | 3 | roles, users (name and type only), sessions |

Never requested: `IMPORTED PRIVILEGES`, `MANAGE GRANTS`, `IMPORT SHARE`, `USAGE` on functions, `READ` on stages.

Known limits: `ACCOUNT_USAGE` has a latency of 45 minutes to 3 hours (up to 2 days for load history). The report shows the latency of each read. `ACCESS_HISTORY` requires Enterprise edition; without it, reads per table are inferred from `TABLE_QUERY_PRUNING_HISTORY` with medium confidence.

## BigQuery

`canot bigquery-setup --project MY_PROJECT --member user:…` writes the `gcloud` script for a `canotScanner` custom role. Required permissions: `bigquery.jobs.create`, `bigquery.jobs.listAll`, `bigquery.datasets.get`, `bigquery.tables.list`, `bigquery.tables.get`, `bigquery.tables.getIamPolicy`, `bigquery.routines.list`, `bigquery.routines.get`, `bigquery.models.list`, `bigquery.models.getMetadata`, `bigquery.reservations.list`, `bigquery.reservationAssignments.list`, `bigquery.capacityCommitments.list`, `bigquery.bireservations.get`, `bigquery.config.get`. Optional: `bigquery.rowAccessPolicies.list`, `bigquery.transfers.get`, `bigquery.connections.list`, `bigquery.dataPolicies.list`.

Never: `bigquery.tables.getData`, nor the `bigquery.user` or `jobUser` roles.

Method notes:
- One `INFORMATION_SCHEMA` query per region (regions are not hierarchical: `EU` does not cover `europe-west1`). BigQuery bills at least 10 MB per query of this type, without caching: the scan caps each query and runs a dry run first.
- Volumes come from `TABLE_STORAGE` when it is readable, otherwise from the `tables.get` API. Canot never changes the project settings to enable it.
- Under a row access policy, `total_bytes_billed` is empty: the report says so instead of showing 0.
- The free tier is shared across the billing account: it cannot be attributed per project.

## dbt

`canot scan dbt://PROJECT_PATH` reads the project's artifacts, with no network and no dbt command: `target/manifest.json` (required), plus `target/run_results.json` and `target/catalog.json` if they exist. `PATH` can be the project, its `target/` folder or the `manifest.json` file; a project folder passed without `dbt://` is recognized too. Without a manifest, `dbt parse` produces one by reading the project files, without connecting to the warehouse. A single level (1): `--level` is brought down to 1.

| Read | What is kept |
|---|---|
| `manifest.json` | models (materialization, target relation, dependencies), tests, sources, seeds, snapshots, exposures, metrics, project macros, packages (number of macros), adapter and dbt version, dependency graph (`liens`: every `ref()` and `source()`) |
| Compiled SQL of models, tests, snapshots | sha256 fingerprint of the normalized shape (literals masked), line count, functions and constructs specific to the adapter's dialect (sqlglot), functions missing from DuckDB, DuckDB translation category (C0 to C7, same rules as for queries); never the text |
| `run_results.json` | status of the last invocation per node, failures, failing rows of tests, last write date of tables |
| `catalog.json` | columns and types (skeleton for the DuckDB EXPLAIN), rows and bytes if the adapter provides them, pseudonymized owner |

Portability of a transformation: `traduisible` (translatable) when sqlglot translates the SQL to DuckDB (accepted by EXPLAIN if `catalog.json` is read, "not tested" otherwise); `a_reconstruire` (to rebuild) only on a fact that was read: a function missing from DuckDB, an impossible translation, an adapter-specific materialization (for example `dynamic_table`); `non_determine` (undetermined) for a Python model, uncompiled SQL still full of Jinja, or SQL that contains a secret (counted, then discarded).

Findings: F13 for a transformation to rebuild (with the reason), F08 for a model whose SQL depends on Jinja and macros (`{% %}` blocks, calls other than `ref`, `source`, `config`; dbt's generic tests do not count), F11 for the project DAG (last invocation, failures; scheduling not read: it lives in the orchestrator), F01 for an external source (location reduced to the bucket), F12 for a missing artifact. Consumption, billed volumes and permissions are not in these files: the report marks them "not read".

Validated on 2026-10-19 on the test bench project `lab/dbt` (artifacts from a real `dbt build`, dbt 1.12.6, Snowflake adapter): 8 models (4 staging views, 4 mart tables), 8 tests, 4 sources, 19 links; findings F11 and F12 (no `catalog.json`). Copy of these artifacts and frozen outputs: `tests/fixtures/dbt/banc`, `tests/golden/cli_scan_dbt`.

### P3 evidence: `canot replay-dbt`

`canot replay-dbt dbt://PROJECT --base copy.duckdb` (or `--iceberg DESTINATION --drill-out FOLDER` for the Iceberg copy written by `canot drill`) replays each SQL model in a local DuckDB, in graph order, from the source tables of the copy, then compares its output to the table the platform had materialized, using the same criteria as `canot drill`: row count, per-column statistics, content fingerprint. The types of the replayed output are aligned with those of the original table before the comparison; each conversion is recorded. The result (`replay-dbt.json`) and the signed evidence log (`evidence.jsonl`, to check with `canot verify-evidence`) classify each model:

| Status | Meaning |
|---|---|
| `identique` (identical) | replayed, compared, no discrepancy within tolerances |
| `different` | replayed, compared, discrepancy (rows, statistics or fingerprint) |
| `non_compare` (not compared) | replayed, but no original table in the copy (a view); its output feeds the downstream models |
| `non_rejouable` (not replayable) | with the reason: Python language, incremental (depends on prior state), ephemeral (replayed through the models that use it), adapter-specific function or materialization, translation rejected, table missing from the copy, dependency not replayed |

The compiled SQL must be in the manifest (`dbt compile` or `dbt build` puts it there). Tested on 2026-10-19 on the test bench artifacts and a DuckDB copy built by the tests (original tables written by hand, no network): 4 identical mart tables, 4 views replayed but not compared, an injected discrepancy detected, including when reading the Iceberg copy written by `canot drill`.

First replay on a real copy (2026-10-19): `canot drill` of the test bench Snowflake account (RAW and marts schemas) to a local Iceberg copy, then `canot replay-dbt --iceberg`, with no new query to the platform. Result: `int_order_totals` identical, 4 views replayed but not compared, 3 marts different (`dbt_fct_orders`: 150,000 rows originally, 25,325 replayed; `dbt_customer_ltv`; `dbt_daily_revenue`). These discrepancies are correct: the customers table is protected by a row access policy and the drill's read role only sees part of it. The copy of the inputs is incomplete and the replay shows it: this is exactly what P3 must detect (finding F10 of the Snowflake scan points out that a copy requires a principal not filtered by these policies).

The same run revealed a false discrepancy, since fixed: `int_order_totals` was classified as different although rows and statistics were equal. Cause, column by column: `ORDER_ID` and `ITEMS` equal (the recorded conversion `ITEMS : HUGEINT -> INTEGER` changes no value); `BASKET_VALUE` is a sum of floats (`UNIT_PRICE` as FLOAT) that Snowflake and DuckDB add up in a different order. 36,971 values differ in the last bit (`121.79` versus `121.79000000000002`, relative difference at most 4e-16), and the content fingerprint hashes the exact text. Now, when rows and statistics are equal and only the fingerprint differs, the replay recomputes the fingerprint on both sides with float columns rounded to 12 significant digits (finer than the relative tolerance of 1e-9). If it is then equal, the model is "identical within tolerance" and the report records the rounded columns, the number of values that differ in the last bit and the maximum measured relative difference (`verification.empreinte_flottants`). A larger discrepancy stays "different".

**Time zone of the replay.** A timestamp with time zone is copied to Iceberg as an instant in UTC; `canot drill` logs the source zone with the conversion (`tz` in the `conversions` of `report.json`). The replay applies it: when the inputs of the models carry exactly one source zone, the DuckDB session runs in that zone, so a date derived from a timestamp (`cast(... as date)`, `date_trunc`) falls on the same day as on the platform. With no source zone, the session runs in UTC, explicitly: the result no longer depends on the machine's zone. With several source zones, it runs in UTC and the report states it ("several source time zones"): the models that read them are marked not determined and never count as a complete proof. The zone applied and the columns it comes from are in `replay-dbt.json` (`timezone`) and in the drill report. The conversions come from the drill run itself (`canot drill dbt://`), from the drill's `report.json` (`--iceberg --drill-out`), and are absent with `--base` (UTC). What the rule does not cover: a `TIMESTAMP_TZ` keeps the offset of each value, and offsets that do not follow the zone's rules (a fixed offset across daylight saving time, or offsets that vary by row) are not recovered from the instant; BigQuery `TIMESTAMP` carries no zone (its dates are cut in UTC by default), so the replay falls back to UTC, which matches it.

### Exit exercise of a dbt project: `canot drill dbt://`

`canot drill dbt://PROJECT DESTINATION --target NAME` chains the two steps above with the copy in between: it reads `profiles.yml` to find the warehouse, copies the relations the manifest names (sources, seeds, snapshots, table and incremental models; views are replayed, not copied), compares them, replays the models on the copy and signs one evidence log for the whole. Unlike the scan, it connects to the warehouse: read-only, with the credentials and the role of the dbt profile. Profile resolution, adapters and what is not copied: [commands](commands.md#canot-drill-dbt).

Tested on 2026-10-19 on the test bench: `canot drill dbt://lab/dbt <folder> --target snowflake --profiles-dir lab/dbt`, Snowflake trial account, profile `canot_lab` (role `CANOT_LAB`, warehouse `CANOT_WH`, key pair from `SNOWFLAKE_PRIVATE_KEY_PATH`). Measured:

- **Relations**: 8 copied (4 sources in `RAW`, 4 mart tables in `DBT_dbt_mart`), 24.1 MB, 10.5 s for the whole exercise; 4 staging views not copied, with their reason. The 8 copies are identical to the platform (rows, statistics, fingerprint).
- **Policies**: read with the profile's role: `RAW.CUSTOMERS` carries the row access policy `RAP_COUNTRY` and the masking policy `MASK_EMAIL` on `EMAIL`. The report states that the copy holds what `CANOT_LAB` can read.
- **Replay (P3)**: `int_order_totals` identical, 4 views replayed but not compared, 3 marts different: `dbt_fct_orders` (150,000 rows on both sides, latest `ORDER_DATE` 2025-08-22 on the platform, 2025-08-23 on the replay), `dbt_customer_ltv` (19,924 rows on both sides, `FIRST_ORDER` and `LAST_ORDER` shifted), `dbt_daily_revenue` (14,399 rows on the platform, 14,423 replayed).
- **Why**: `RAW.ORDERS.ORDER_TS` is a `TIMESTAMP_TZ`; the copy keeps the instant in UTC (the drill logs this conversion as lossy) and `cast(order_ts as date)` in `stg_orders` gave a different day for evening orders, the replay then running in the machine's zone (Europe/Paris). Entered in the [discrepancy catalogue](discrepancies.md).
- **With the time zone rule** (same day, one more run): the replay ran in `America/Los_Angeles`, the zone logged for `RAW.ORDERS.ORDER_TS`. The 3 marts remain different but closer: `dbt_daily_revenue` has 14,409 replayed rows instead of 14,423 (platform 14,399); `dbt_fct_orders` and `dbt_customer_ltv` keep the platform's row counts, only the latest dates differ. Measured on the copy, offline: the platform's `ORDER_DATE` equals the date of `ORDER_TS` at a fixed offset of -08:00 for all 150,000 orders; in the `America/Los_Angeles` zone, 4,183 orders fall on another day (evening orders during daylight saving time, at -07:00); in UTC, 49,930. The lab script builds `ORDER_TS` as `dateadd(minute, …, '2024-01-01'::timestamp_tz)`: every value keeps the January offset, -08:00, all year. This is the case the rule does not cover (offsets that do not follow the zone's rules); the report says so for each of the 3 marts and no longer blames the row access policy, whose effect is not measured here.
- **Evidence**: one folder, `report.html`, `report.json`, `replay-dbt.json`, a log of 32 entries accepted by `canot verify-evidence`; no user name, secret or key path in any file or printed line.

This run differs from the first replay above: with the profile's role `CANOT_LAB`, the row access policy hides no customer that has orders (the replayed `dbt_fct_orders` has the platform's 150,000 rows), so the remaining differences come from the time zone. Both are the correct result for their reader; the evidence names the cause each time instead of hiding the difference.

## Reading the figures

Every figure carries its nature: **measured** (read as is), **computed** (derived from measurements, formula given), **estimated** (range and assumptions), **not read** or **not estimated** (with the reason). Prices are dated, sourced reference points; pass `--credit-price` with your contract price to replace them. The read cost and the duration of a copy are planning estimates: `canot drill` replaces them with measurements.

Replayability measures how many query shapes are translated to DuckDB **and accepted** by it, on an empty skeleton typed from your columns. **Accepted does not mean identical result**: only `canot drill` compares results. Queries on tables outside the scope that was read, queries whose text the platform does not provide and metadata queries are counted separately, outside the rate.

## What the scan does not see

Reads through the Storage Read API and queries run from other projects (BigQuery), consumers of shares and `RESULT_SCAN` (Snowflake), jobs that run less often than the window that was read (365 days on Snowflake, 90 to 180 days on BigQuery). The actual window is shown next to each finding.
