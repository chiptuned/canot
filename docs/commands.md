# Canot: the commands

Canot rehearses your exit from a data platform, inside your own accounts. One command is enough:

```bash
uvx canot drill SOURCE DESTINATION
```

`SOURCE` is the platform you would leave (Snowflake, Databricks, BigQuery), `DESTINATION` is where the copy is written. Replace the `MY_…` values with your own. Canot copies the tables to Apache Iceberg, compares everything, replays your most expensive queries and writes a signed evidence log. It sends nothing to us: no telemetry.

```bash
# Snowflake to a local folder
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg

# Databricks to the S3 storage of your choice
uvx canot drill "databricks://MY_WORKSPACE.cloud.databricks.com/sql/1.0/warehouses/MY_WAREHOUSE_ID?catalog=MY_CATALOG" "s3://my-bucket/canot?endpoint=https://s3.internal:9000&region=us-east-1"

# BigQuery to a European hosting provider
uvx canot drill "bigquery://MY_PROJECT/MY_DATASET?location=EU" scaleway:fr-par --bucket my-bucket
```

The Snowflake, BigQuery and Databricks connectors ship with Canot: nothing to add. Without `uv`, `pipx run canot …` or `pip install canot` work the same way.

## The commands

| Command | What it does |
|---|---|
| `canot scan SOURCE` | Read-only assessment: inventory, volumes, consumption, what does not move as-is, query replayability. Writes `scan.html` (to share), `scan.json` and CSV files. See [scan](scan.md). |
| `canot drill SOURCE DESTINATION` | Full exit exercise: copy, table-by-table comparison, query replay, signed evidence log. |
| `canot demo` | A 30-second demo, no account or data needed: the TPC-H dataset (the standard database benchmark, generated locally, 100 MB by default; `--sf 1` for about 1 GB) and a full exercise. It prints the two commands it runs (`demo-data`, `drill`) and accepts a destination: `canot demo aws:eu-west-3 --bucket MY-BUCKET`. Offline, it falls back to a fictional dataset. |
| `canot verify-evidence FOLDER` | Verifies the hash chain and signature of an evidence log. `--expect-pub` rejects a log signed by a different key. |
| `canot teardown DESTINATION` | Deletes the copied data. |
| `canot snowflake-setup` | Writes the read-only access SQL script for an administrator to review and run. `--generate-key` creates the key pair. |
| `canot bigquery-setup` | Writes the `gcloud` script for the read-only custom role. |
| `canot init-tolerances` | Writes a comparison tolerances file to review together. |
| `canot demo-data PATH` | Creates a demo DuckDB database. |
| `canot replay-dbt dbt://PROJECT --base COPY.duckdb` | P3 evidence on a dbt project: replays the SQL models in DuckDB from the copy of the sources (a DuckDB database, or `--iceberg` for the copy written by `canot drill`), compares each output to the original table (rows, statistics, fingerprint) and signs an evidence log. See [scan](scan.md#p3-evidence-canot-replay-dbt). |
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
| `--key PATH` | Signing key (default `~/.canot/evidence.key`, or the `CANOT_EVIDENCE_KEY_PATH` variable). |

Output in `--out`: `report.html` (readable by a committee), `report.json`, the evidence log (`evidence.jsonl`, `evidence.sig`, `evidence.pub`) and `reread.sql`. The signature attests that the log has not been modified since it was signed. It attests neither the author nor the date.

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
| dbt (project) | `dbt://PROJECT_PATH`: the project, its `target/` folder or `manifest.json`; scan only (artifacts read offline), evidence through `canot replay-dbt` | none: no network, no dbt command |

`canot connectors` shows, for each one, what Canot can do (full or light inventory, export, evidence) and whether it is tested. For dbt, P3 evidence goes through `canot replay-dbt`.

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
| `explain_report(path)` | Reads an exercise `report.json`: discrepancies, tables and queries involved, known causes from the catalog. Also reads a `replay-dbt.json` (dbt P3 evidence): identical, different and non-replayable models, with their reasons. |

The tools read files you point them to. They open no connection to your platforms.

For agents that read the web: [canot.io/llms.txt](https://canot.io/llms.txt) and [canot.io/llms-full.txt](https://canot.io/llms-full.txt).

## Going further

- [scan](scan.md): what is read, with which permissions, and the guarantees.
- [on-prem](on-prem.md): exit to a local folder or your own storage.
- [targets](targets.md): hosting providers and countries.
- [discrepancies](discrepancies.md): what two engines return differently, measured.
- Help running it on your scope: [canot.io](https://canot.io/#contact).
