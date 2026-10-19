<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-dark.svg">
    <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-light.svg" alt="Canot: prove you can leave" width="100%">
  </picture>
</p>

<p align="center">
  <b>Prove you can leave.</b><br>
  An exit exercise for your data platform, run inside your own accounts.
</p>

<p align="center">
  <img alt="license Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-1F3A5F?style=flat-square">
  <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-1F3A5F?style=flat-square">
  <img alt="no telemetry" src="https://img.shields.io/badge/telemetry-none-2F7A4B?style=flat-square">
  <img alt="Snowflake, BigQuery" src="https://img.shields.io/badge/Snowflake%20·%20BigQuery-1F3A5F?style=flat-square">
</p>

<p align="center">
  <a href="https://canot.io"><b>canot.io</b></a> ·
  <a href="docs/commands.md">Documentation</a> ·
  <a href="docs/discrepancies.md">Measured discrepancies</a> ·
  <a href="#-try-it">Try it</a> ·
  <a href="#-why">Why</a> ·
  <a href="README.fr.md">Français</a>
</p>

---

## ▶ Try it

```bash
uvx canot demo
```

No account, no data: it runs the TPC-H benchmark dataset through a full exit exercise (copy to Apache Iceberg, comparison, signed evidence report) in a few seconds.

<p align="center">
  <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/demo.svg" alt="uvx canot demo copies the 8 TPC-H tables, compares everything and flags a discrepancy" width="860">
</p>

## ▶ Connectors

`canot connectors` lists them with what each can do (inventory, export, evidence) and whether it has been validated.

| Connector | Source URL | Status |
|---|---|---|
| Snowflake | `snowflake://ACCOUNT/DATABASE` | available, tested: full scan, copy, comparison, query replay |
| BigQuery | `bigquery://PROJECT/DATASET` | available, tested: full scan, copy, comparison, query replay |
| dbt | `dbt://PROJECT_PATH` | available, tested: scan of the project files, replay of the models (`canot replay-dbt`) |
| DuckDB | `file.duckdb` | available, tested: light scan, copy, comparison |
| Databricks | `databricks://HOST/HTTP_PATH?catalog=…` | written, not yet validated on a real account |
| Amazon Redshift | `redshift://CLUSTER/DATABASE` | roadmap, no connector yet |
| Microsoft Fabric / Azure Synapse | `fabric://WORKSPACE` | roadmap, no connector yet |
| Teradata | `teradata://HOST/DATABASE` | roadmap, no connector yet |
| Oracle | `oracle://HOST/SERVICE` | roadmap, no connector yet |
| SAP Datasphere | `sap://TENANT` | roadmap, no connector yet |
| ClickHouse | `clickhouse://HOST/DATABASE` | roadmap, no connector yet |

Running Canot on a platform without a connector prints how to ask for it. A connector is written and validated with you, on your own account: [tell us what you run](https://canot.io/en/review.html).

## 🧭 Why

|  |  |  |
|---|---|---|
| **The imbalance** | **The pressure** | **The hidden risk** |
| Snowflake and Databricks offer free tools to *move in*. Nobody offers the tool that checks the *way out*. | DORA requires financial entities to have a **tested** exit plan. In France, data transfer when switching cloud providers already costs €0; on **12 Jan 2027**, all switching charges disappear (EU Data Act). | The work remains: duration, discrepancies and what does not move as-is stay unknown until someone actually runs the exit. |

Canot runs the rehearsal. You stay with your provider.

## ▶ On your data

```bash
uvx canot scan "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER"    # read-only assessment
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg   # full exit exercise, to a local folder
```

Exit to your own infrastructure (folder, NAS, self-hosted S3 storage), commands, permissions and agent access (MCP): see the [documentation](docs/commands.md). `uvx canot connectors` lists the supported platforms, what Canot can do on each (inventory, export, evidence) and whether the connector is tested. Canot is **not** a certification: it produces a dated record of measurements on the scope you ran. The website, [canot.io](https://canot.io), explains the rest.
