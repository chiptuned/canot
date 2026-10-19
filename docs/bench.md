# Benchmark

Real measurements, with their conditions. They describe how Canot behaves, not how a platform behaves: throughput depends on the network, the machine and the source warehouse.

## TPC-H SF10 from Snowflake to a local disk (2026-10-17)

| | |
|---|---|
| Source | Snowflake, trial account, TPC-H SF10 sample dataset, XSMALL warehouse |
| Destination | local folder (Apache Iceberg, 2.8 GB of Parquet files) |
| Volume | 8 tables, **86,586,082 rows**, about 10.6 GB read (largest table: 59,986,052 rows, 7.3 GB) |
| Result | 8 tables out of 8 with identical content (row counts and per-column statistics recomputed in the source engine; content fingerprint of the rows as Canot read them against the copy); 2 queries out of 2 identical |
| Total duration | 31 min 36 s (copy, comparison and replay included) |
| Copy throughput | about 7 MB/s, limited by reads from the trial warehouse |
| Memory | 2.4 GB peak on the machine running the exercise (MacBook Air), for 10.6 GB of data: the comparison reads the copy in batches |
| Consumption | about 0.41 Snowflake credits |
| Version | Canot 0.1.0 + batch verification fixes, DuckDB 1.5.6, pyiceberg 0.12 |

Known limits: query replay loads the tables the queries touch into memory (`--replay-max-gb` option, 2 GB by default); here, only queries on small tables were replayed. No test beyond 10 GB.
