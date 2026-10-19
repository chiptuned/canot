<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-dark.svg">
    <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-light.svg" alt="Canot" width="100%">
  </picture>
</p>

<p align="center"><b>Rehearse leaving your data platform, inside your own accounts.</b><br><a href="README.md">Français</a> · <a href="https://canot.io">canot.io</a></p>

## Try it

```bash
uvx canot demo
```

No account, no data. Canot generates **TPC-H**, the standard database benchmark (8 tables, 22 queries, up to 1 GB of data at scale factor 1), copies it to Apache Iceberg, compares everything, replays TPC-H queries on both sides and writes a signed evidence report. A 100 MB version by default (a few seconds); `uvx canot demo --sf 1` runs scale factor 1, about 1 GB (around 20 seconds on a laptop). The command prints the two commands it runs (`canot demo-data`, then `canot drill`): copy them to change the destination, for example `uvx canot demo aws:eu-west-3 --bucket MY-BUCKET`.

Canot copies tables from BigQuery, Snowflake or Databricks into Apache Iceberg on storage you control, compares each table (row counts, per-column statistics, content fingerprint), replays your key queries on both sides, and writes a report with a hash-chained, signed log.

It runs entirely in your accounts. No telemetry. It is **not** a certification: it is a dated record of measurements on the scope you ran.

## On your data (read-only)

```bash
uvx canot scan "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER"
```

Snowflake: `canot snowflake-setup --generate-key` prints the SQL a platform admin reviews and runs (read-only role, small warehouse, credit cap, public key).

Documentation (in French): [commands, on-prem exit, scan rights, MCP for agents](docs/commandes.md).

## What it finds

Real differences between engines, with the query, both results, versions and date: see the [catalogue of divergences](docs/ecarts.md) (in French for now).

Apache-2.0 · [security policy](SECURITY.md)
