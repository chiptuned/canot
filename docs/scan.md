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

## Reading the figures

Every figure carries its nature: **measured** (read as is), **computed** (derived from measurements, formula given), **estimated** (range and assumptions), **not read** or **not estimated** (with the reason). Prices are dated, sourced reference points; pass `--credit-price` with your contract price to replace them. The read cost and the duration of a copy are planning estimates: `canot drill` replaces them with measurements.

Replayability measures how many query shapes are translated to DuckDB **and accepted** by it, on an empty skeleton typed from your columns. **Accepted does not mean identical result**: only `canot drill` compares results. Queries on tables outside the scope that was read, queries whose text the platform does not provide and metadata queries are counted separately, outside the rate.

## What the scan does not see

Reads through the Storage Read API and queries run from other projects (BigQuery), consumers of shares and `RESULT_SCAN` (Snowflake), jobs that run less often than the window that was read (365 days on Snowflake, 90 to 180 days on BigQuery). The actual window is shown next to each finding.
