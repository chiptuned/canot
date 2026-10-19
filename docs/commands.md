# Canot: the commands

Canot rehearses your exit from a data platform, inside your own accounts. One command is enough:

```bash
uvx canot drill SOURCE DESTINATION
```

`SOURCE` is the platform you would leave (Snowflake, Databricks, BigQuery), or a dbt project that runs on one, `DESTINATION` is where the copy is written. Replace the `MY_…` values with your own. Canot copies the tables to Apache Iceberg, compares everything, replays your most expensive queries and writes a signed evidence log. It sends nothing to us: no telemetry.

```bash
# Snowflake to a local folder
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg

# Databricks to the S3 storage of your choice
uvx canot drill "databricks://MY_WORKSPACE.cloud.databricks.com/sql/1.0/warehouses/MY_WAREHOUSE_ID?catalog=MY_CATALOG" "s3://my-bucket/canot?endpoint=https://s3.internal:9000&region=us-east-1"

# BigQuery to a European hosting provider
uvx canot drill "bigquery://MY_PROJECT/MY_DATASET?location=EU" scaleway:fr-par --bucket my-bucket

# A dbt project: the tables it uses are copied and compared, then its models are replayed on the copy
uvx canot drill dbt://./my_dbt_project ./iceberg --target prod
```

The Snowflake, BigQuery and Databricks connectors ship with Canot: nothing to add. Without `uv`, `pipx run canot …` or `pip install canot` work the same way.

## The commands

| Command | What it does |
|---|---|
| `canot scan SOURCE` | Read-only assessment: inventory, volumes, consumption, what does not move as-is, query replayability. Writes `scan.html` (to share), `scan.json` and CSV files. See [scan](scan.md). |
| `canot drill SOURCE DESTINATION` | Full exit exercise: copy, table-by-table comparison, query replay, signed evidence log. With `dbt://PROJECT` as the source: reads the warehouse named by the dbt profile, copies the relations the project uses, then replays its models on the copy (P1 to P3, one evidence folder). See [`canot drill dbt://`](#canot-drill-dbt). |
| `canot demo` | A 30-second demo, no account or data needed: the TPC-H dataset (the standard database benchmark, generated locally, 100 MB by default; `--sf 1` for about 1 GB) and a full exercise. It prints the two commands it runs (`demo-data`, `drill`) and accepts a destination: `canot demo aws:eu-west-3 --bucket MY-BUCKET`. Offline, it falls back to a fictional dataset. |
| `canot verify-evidence FOLDER` | Verifies the hash chain and signature of an evidence log. `--expect-pub` rejects a log signed by a different key. |
| `canot teardown DESTINATION` | Deletes the copied data. |
| `canot snowflake-setup` | Writes the read-only access SQL script for an administrator to review and run. `--generate-key` creates the key pair. |
| `canot bigquery-setup` | Writes the `gcloud` script for the read-only custom role. |
| `canot init-tolerances` | Writes a comparison tolerances file to review together. |
| `canot demo-data PATH` | Creates a demo DuckDB database. |
| `canot replay-dbt dbt://PROJECT --base COPY.duckdb` | P3 evidence on a dbt project: replays the SQL models in DuckDB from the copy of the sources (a DuckDB database, or `--iceberg` for the copy written by `canot drill`), compares each output to the original table (rows, statistics, fingerprint; proof level per model) and signs an evidence log. See [scan](scan.md#p3-evidence-canot-replay-dbt). |
| `canot connectors` | Lists the known platforms: URL format, inventory (`scan`), export (`drill`), evidence type, status (tested, or written but untested) and the `pip` extra to install. |
| `canot mcp` | MCP server: the Canot documentation and explanations of your reports, for an agent. See [Using it with an agent](#using-it-with-an-agent-mcp). |

Every command accepts `--debug` (full traceback on error).

## `canot drill`

Useful options:

| Option | Purpose |
|---|---|
| `--bucket NAME` | Required with a hosting provider shortcut (`scaleway:fr-par`, `ovh:gra`, `outscale:eu-west-2`, `ionos:eu-central-1`, `stackit:eu01`, `aws:eu-west-3`). |
| `--catalog URI` | Iceberg catalog: `sqlite:///…`, or `env:VARIABLE_NAME` for PostgreSQL (`canot[postgres]`). Default: a SQLite file in the output folder. |
| `--out FOLDER` | Where to write the report and the evidence log (default `canot-out`). |
| `--tables SCHEMA.TABLE …` | Restrict to specific tables. |
| `--queries FILE.sql` | Replay your own queries. Default: the most expensive ones from the platform's query history. |
| `--top N` | Number of history queries to replay (default 20). |
| `--tolerances FILE.toml` | Comparison tolerances (rounding, timestamps). |
| `--credit-price USD` | The Snowflake credit price in your contract. Without it: a dated range based on the list price. |
| `--replay-max-gb N` | Maximum size of a table loaded in memory to replay queries (default 2). |
| `--lang en\|fr` | Language shown first in `report.html`. The report always holds both English and French, switched at the top of the page; this only picks the default. Default: your locale (`fr…` gives French), else English. The terminal output and `report.json` stay in English. Also accepted by `canot demo`. |
| `--key PATH` | Signing key (default `~/.canot/evidence.key`, or the `CANOT_EVIDENCE_KEY_PATH` variable). |
| `--profiles-dir FOLDER` | dbt project only: the folder that holds `profiles.yml`. |
| `--target NAME` | dbt project only: the profile target to read (also `dbt://PROJECT?target=NAME`). |

Output in `--out`: `report.html` (readable by a committee), `report.json`, the evidence log (`evidence.jsonl`, `evidence.sig`, `evidence.pub`) and `reread.sql`. The signature attests that the log has not been modified since it was signed. It attests neither the author nor the date.

### Proof levels

Every comparison states the level of proof it reached, as a stable identifier: per table in `report.json` (`checks[].proof_level`, with `proof_note` saying what was compared; tables that could not be compared are listed in `not_compared` with the reason), in the evidence log (`table_check`, `table_check_error` and failed `copy` entries) and in the report (a column of the Tables section and a Proof level section). The level of the exercise (`summary.proof_level`, also in `drill-share.json` and in the summary printed by `canot drill` and `canot demo`: `Proof level: full_aggregates`) is the **lowest** level over its tables, never an average; `summary.tables_by_proof_level` counts the tables per level.

| Level | Meaning | Today |
|---|---|---|
| `full_fingerprint` | a content fingerprint computed in both engines from a canonical form of every row, then compared | not implemented yet |
| `full_aggregates` | row count and, per column, non-null count, min, max and sum (numeric columns) recomputed in the source engine over the whole table and compared with the copy; plus a fingerprint of the rows as Canot read them, compared with the copy | what Canot does |
| `partition_sampled` | full aggregates everywhere, plus a full fingerprint in partitions drawn with a recorded seed | not implemented yet |
| `row_sampled` | rows selected by a hash of their key, compared value by value | not implemented yet |
| `metadata_only` | only catalog metadata compared (structure, declared row count): content not read | produced by the dbt replay when only the column lists could be compared (they differ) |
| `not_compared` | nothing compared: copy error, comparison error, or table not copied; the reason is given | produced |

From weakest to strongest: `not_compared`, `metadata_only`, `row_sampled`, `partition_sampled`, `full_aggregates`, `full_fingerprint`.

What `full_aggregates` does not prove: a value changed inside a text column that keeps the same count, minimum and maximum would not be detected by the recomputation in the source engine, and the content fingerprint only shows that the copy matches what Canot read, not that Canot read the source faithfully. Columns converted with a possible loss (nanoseconds, for instance) are compared only within the precision of the target type, and `proof_note` names them. A content fingerprint computed in both engines is not implemented yet.

## `canot drill dbt://`

```bash
uvx canot drill dbt://PROJECT_PATH DESTINATION [--target NAME] [--profiles-dir FOLDER]
```

The exit exercise of a dbt project, in one command:

1. **Reads the project**: `dbt_project.yml` (the profile name), `profiles.yml` and `target/manifest.json` (`dbt parse` produces it from the project files, without connecting to the warehouse). `profiles.yml` is found with `--profiles-dir`, else `DBT_PROFILES_DIR`, else the project folder, else `~/.dbt/profiles.yml`; the target is `--target` (or `?target=NAME` in the URL), else the profile's default target. `{{ env_var('NAME') }}` and `{{ env_var('NAME', 'default') }}` are resolved; no other Jinja is evaluated and no code is run: a value that needs more is refused with the field named.
2. **Works out the warehouse and the relations**: the adapter of the target gives the platform; the manifest gives the relations, under the names dbt uses (database, schema, alias). Copied: every source, seed and snapshot, every model materialized as `table` or `incremental` (a Python model's table is copied and compared, the model is not replayed). Not copied, with the reason in the report: views (they hold no data; they are replayed and proven through the tables that read them), ephemeral models (no relation), materialized views and dynamic tables (maintained by the platform, to rebuild), relations in another database, and relations the warehouse does not show to the profile's role.
3. **Copies and compares** those tables to Apache Iceberg: the same copy, comparison and query replay as `canot drill`.
4. **Replays the models** on the copy and compares each output with the original table, as [`canot replay-dbt`](scan.md#p3-evidence-canot-replay-dbt) does: matches, different, not compared, not replayable, with a proof level per model.
5. **Writes one evidence folder** in `--out`: `report.html` and `report.json` (the drill, with a `dbt` section: relations copied and not copied, replay, the explanation of each difference), `replay-dbt.json`, one signed evidence log (`canot verify-evidence` checks it) and `reread.sql`.

| Adapter | Connection read from the profile | Credentials |
|---|---|---|
| `snowflake` | account, user, role, warehouse, database; the schemas of the relations | `private_key_path` (becomes `SNOWFLAKE_PRIVATE_KEY_PATH` unless already set) or a password from the environment; `externalbrowser` and inline keys are not supported yet |
| `bigquery` | project (`execution_project` for billing), location | `method: oauth` (application default credentials) or `service-account` (the keyfile path becomes `GOOGLE_APPLICATION_CREDENTIALS` unless already set) |
| `databricks` | host, http_path, catalog | token from the environment (`DATABRICKS_TOKEN`) |
| `duckdb` | path (relative to the project folder) | none |

Another adapter (postgres, redshift, sqlserver…) gets the same message as a platform without a connector, with how to ask for one.

**Your credentials, read-only.** Canot connects with the credentials and the role the profile names, only reads, and never adds a right. What the role cannot see is reported as not copied, never worked around. A secret (password, token, key passphrase) is accepted only as `{{ env_var('…') }}`: written in clear in `profiles.yml`, it is refused. Secrets are never printed nor written: the evidence log records the adapter, account or project, database, role and schemas, never the user, a secret or a key path.

**Reading the result.** A model that differs while its inputs match their copy is explained with what the run measured, measured causes first: an input copied with a lossy conversion (a `TIMESTAMP_TZ` reduced to UTC: the replay applies its source zone, see [scan](scan.md#p3-evidence-canot-replay-dbt); dates that still differ point to per-value offsets the rule cannot recover), then a row access policy on its inputs when the row counts differ (Snowflake: policies read with the profile's role, or reported as not readable). Under such a policy the copy holds what the role can read: a table that dbt built with a role that saw more cannot be reproduced from it. That is the expected result for this reader, not a translation error (finding F10 of `canot scan`). Exit code: 0 when every copied table matches and no model differs, 1 otherwise.

Tested on 2026-10-19 on the Snowflake test bench (`lab/dbt`, target `snowflake`); BigQuery and Databricks profiles are written but not yet validated on a real account. Details: [scan](scan.md#exit-exercise-of-a-dbt-project-canot-drill-dbt).

## `canot scan`

```bash
uvx canot scan "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" --level 2
```

`--level 1|2|3` (default 3) sets how deep to read; `--credit-price` replaces the list price; `--include-sql-text` also writes the raw SQL to a separate file (never by default). Details of the reads, permissions and guarantees: [scan](scan.md).

## The sources

| Source | Format | Authentication |
|---|---|---|
| BigQuery | `bigquery://PROJECT/dataset1,dataset2?location=EU`; without a dataset: all datasets in the project; another project: `bigquery://BILLING_PROJECT/data-project.dataset` | Google application default credentials (`gcloud auth application-default login`) |
| Snowflake | `snowflake://ACCOUNT/DATABASE?user=…&warehouse=…&role=…&schemas=A,B` | key pair: `SNOWFLAKE_PRIVATE_KEY_PATH` variable (recommended) or `SNOWFLAKE_PASSWORD` |
| Databricks | `databricks://HOST/WAREHOUSE_HTTP_PATH?catalog=…` | `DATABRICKS_TOKEN` variable (read token); connector not yet tested on a real account |
| DuckDB | `house.duckdb` | none |
| dbt (project) | `dbt://PROJECT_PATH`: the project, its `target/` folder or `manifest.json`. `canot scan` reads the artifacts offline; `canot drill dbt://PROJECT DESTINATION` (`?target=NAME`, `--profiles-dir`) reads the warehouse named by `profiles.yml` (Snowflake, BigQuery, Databricks, DuckDB) | scan: none, no network, no dbt command. drill: the credentials of the dbt profile, read-only; secrets only from the environment |

`canot connectors` shows, for each one, what Canot can do (full or light inventory, export, evidence) and whether it is tested. For dbt, `canot drill dbt://` gives P1 to P3 in one folder; `canot replay-dbt` replays the models on a copy that already exists.

## Local or on-prem exit

No cloud needed on arrival: a folder is enough.

```bash
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg
```

Canot writes the copy in Iceberg format to `./iceberg` and the evidence log to `canot-out/`. The copy does not depend on the tool: DuckDB, Spark or Trino can read it. The `reread.sql` file contains the DuckDB commands to recount each table:

```bash
duckdb < canot-out/reread.sql
```

| Destination | Command |
|---|---|
| Local or mounted folder (disk, NAS, network share) | `canot drill SOURCE /path/to/folder` |
| Self-hosted S3-compatible storage (MinIO, Ceph, NetApp…) | `canot drill SOURCE "s3://bucket/canot?endpoint=https://s3.internal:9000&region=us-east-1"` |
| Corporate proxy, internal certificate authority | add `&proxy=http://proxy.internal:3128` and `&ca=/path/ca.pem` to the S3 URL |
| PostgreSQL catalog | `--catalog env:CANOT_CATALOG_URI` (password in the variable, never on the command line) |

Storage keys go through `CANOT_S3_ACCESS_KEY` and `CANOT_S3_SECRET_KEY` (or the `scw` configuration). Status of each case (tested or only wired up): [on-prem](on-prem.md).

## Hosting providers

`scaleway:fr-par`, `ovh:gra`, `outscale:eu-west-2`, `ionos:eu-central-1`, `stackit:eu01`, `aws:eu-west-3`, with `--bucket`. Check each endpoint in the provider's console. SecNumCloud qualification, when it matters to you, depends on the specific offering: see [targets](targets.md).

## Using it with an agent (MCP)

Many teams work with an agent. `canot mcp` gives it the Canot documentation and explains your reports, without sending anything to anyone: the server runs on your machine.

Setup in Claude Code:

```bash
claude mcp add canot -- uvx canot mcp
```

Or in your client's MCP configuration (Claude Desktop, Cursor, VS Code…):

```json
{
  "mcpServers": {
    "canot": { "command": "uvx", "args": ["canot", "mcp"] }
  }
}
```

Exposed tools:

| Tool | What it does |
|---|---|
| `commands` | Lists the commands and their options. |
| `search_docs(query)` | Searches the documentation and returns the matching passages. |
| `read_doc(topic)` | Returns a full page: `commands`, `scan`, `on-prem`, `targets`, `discrepancies`. |
| `capabilities(platform)` | What Canot can do on each platform (inventory, export, evidence type), with the status (tested, or written but untested) and the recorded test date. Without an argument: all platforms. |
| `explain_scan(path)` | Reads a `scan.json` and summarizes it: findings, reads not performed, estimated figures, what to look into. |
| `explain_report(path)` | Reads an exercise `report.json`: discrepancies, tables and queries involved, known causes from the catalog. Also reads a `replay-dbt.json` (dbt P3 evidence): matching, different and non-replayable models, with their reasons; for a `canot drill dbt://` folder, the `dbt` section: relations not copied and why each model differs. |

The tools read files you point them to. They open no connection to your platforms.

For agents that read the web: [canot.io/llms.txt](https://canot.io/llms.txt) and [canot.io/llms-full.txt](https://canot.io/llms-full.txt).

## Going further

- [scan](scan.md): what is read, with which permissions, and the guarantees.
- [on-prem](on-prem.md): exit to a local folder or your own storage.
- [targets](targets.md): hosting providers and countries.
- [discrepancies](discrepancies.md): what two engines return differently, measured.
- Help running it on your scope: [canot.io](https://canot.io/#contact).
