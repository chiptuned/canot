# Canot

Canot rehearses your exit from a data platform, inside your own accounts: it **scans** what you run (read-only), **copies** the data to the open Apache Iceberg format, **compares** the copy with the source, **replays** your queries and dbt models, and writes a report backed by a signed, hash-chained evidence log.

```bash
uvx canot demo
```

- [The commands](commands.md): `scan`, `drill`, `demo`, `verify-evidence`, `mcp` and the rest.
- [Read-only scan](scan.md): what is read, with which rights, and what each flag means.
- [On-prem exit](on-prem.md) and [targets](targets.md): where the copy can live.
- [Benchmark](bench.md) and [known discrepancies](discrepancies.md): measurements and the differences observed between engines.

Source and issues: [github.com/q4o-paris/canot](https://github.com/q4o-paris/canot). Canot does not certify or guarantee compliance: it produces evidence.
