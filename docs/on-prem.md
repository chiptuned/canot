# On-prem exit

Canot writes the copy in Apache Iceberg format. The destination can be a folder on your network, or S3-compatible storage that you host.

| Destination | Command | Status |
|---|---|---|
| Local or mounted folder (NAS, disk, network share) | `--to /path/to/folder` | tested |
| Self-hosted S3-compatible storage | `--to "s3://bucket/canot?endpoint=https://s3.internal:9000&region=us-east-1"` | tested on a local S3 server (moto) |
| Custom catalog (SQLite elsewhere) | `--catalog sqlite:///path/catalog.db` | tested |
| PostgreSQL catalog | `--catalog env:CANOT_CATALOG_URI` with `pip install 'canot[postgres]'` | wired up, not yet tested against a real PostgreSQL |
| Corporate proxy | `...?proxy=http://proxy.internal:3128` in the S3 URL | wired up, not yet tested |
| Internal certificate authority | `...?ca=/path/ca.pem` in the S3 URL (passed through `AWS_CA_BUNDLE`) | wired up, not yet tested |

Storage keys go through `CANOT_S3_ACCESS_KEY` and `CANOT_S3_SECRET_KEY` (or the `scw` configuration). A catalog password goes in an environment variable, never on the command line: `--catalog env:VARIABLE_NAME`.

## Reading the copy without Canot

Each exercise writes a `reread.sql` file next to the report. It contains the DuckDB commands that count the rows of each copied table:

```bash
duckdb < canot-out/reread.sql
```

The copy therefore does not depend on the tool: any engine that reads Iceberg (DuckDB, Spark, Trino) can read it. For S3 storage, first create a DuckDB secret with your keys (see the file).

## What is not supported

Writing directly to PostgreSQL, ClickHouse or another database engine: the copy stays in Iceberg, which these engines can read. No documented offline installation for now.

## Cleaning up

```bash
canot teardown --to "s3://bucket/canot?endpoint=https://s3.internal:9000&region=us-east-1"
```
