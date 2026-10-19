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
  <a href="#-why">Why</a>
</p>

---

## ▶ Try it

```bash
uvx canot demo
```

No account, no data: Canot generates **TPC-H**, the standard database benchmark (8 tables, 22 queries, up to 1 GB of data at scale factor 1), copies it to Apache Iceberg, compares everything, replays TPC-H queries on both sides and writes a signed evidence report. A 100 MB version by default (a few seconds); `uvx canot demo --sf 1` runs scale factor 1, about 1 GB (around 20 seconds on a laptop). The command prints the two commands it runs (`canot demo-data`, then `canot drill`): copy them to change the destination, for example `uvx canot demo aws:eu-west-3 --bucket MY-BUCKET`.

<p align="center">
  <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/demo.svg" alt="uvx canot demo copies the 8 TPC-H tables, compares everything and flags a discrepancy" width="860">
</p>

Result: 7 tables out of 8 with identical content, 3 queries out of 4 returning the same result. The discrepancy comes from a nanosecond timestamp column added by Canot: Iceberg v2 reduces it to microsecond precision. This is the kind of discrepancy you want to find **before** you leave.

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

<sub>Apache-2.0 · No trackers, no telemetry · [Security policy](SECURITY.md) · [q4o](https://q4o.com)</sub>
