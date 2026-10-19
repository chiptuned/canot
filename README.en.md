<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-dark.svg">
    <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-light.svg" alt="Canot" width="100%">
  </picture>
</p>

<p align="center"><b>Rehearse leaving your data platform, inside your own accounts.</b><br><a href="README.md">Français</a> · <a href="https://canot.io">canot.io</a></p>

## Try it

Thirty seconds, no account, no data. The TPC-H benchmark dataset (generated locally), a full exit exercise, and the evidence report opens in your browser.

```bash
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg
```

No data at hand? `uvx canot demo` runs the TPC-H benchmark dataset in thirty seconds, no account. Canot copies tables from BigQuery, Snowflake or Databricks into Apache Iceberg on storage you control, compares each table (row counts, per-column statistics, content fingerprint), replays your key queries on both sides, and writes a report with a hash-chained, signed log.

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
