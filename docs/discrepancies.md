# Discrepancy catalogue

What two engines return differently for the same query or the same type, **measured** with Canot: the query, the result on each side, the versions and the date. Nothing here is assumed: what is not measured yet is listed separately.

## BigQuery: DATE_TRUNC on a date returns a date, the copy returns a timestamp

*Measured on 2026-10-13. BigQuery (public dataset thelook_ecommerce); DuckDB 1.5.6; sqlglot 30.22; Canot 0.1.0.*

```sql
select date_trunc(date(created_at), month) as m, count(*) as orders
from `bigquery-public-data.thelook_ecommerce.orders` group by m order by m
```

| | |
|---|---|
| **Source** (BigQuery) | `2019-01-01 (DATE type)` |
| **Copy** (DuckDB on the Iceberg copy) | `2019-01-01 00:00:00 (TIMESTAMP type)` |

**Why.** In this measurement, the translated query returns a TIMESTAMP where BigQuery returns a DATE. The days and counts are identical; only the column type changes, which can break a join or an export that expects a date.

**What to do.** Write CAST(... AS DATE) explicitly in the query on the destination side, then rerun the exercise.

## Snowflake: the average of a decimal amount keeps 8 exact decimal places, the copy returns a float

*Measured on 2026-10-16. Snowflake (trial account, TPC-H SF1); DuckDB 1.5.6; Canot 0.1.0.*

```sql
select l_returnflag, l_linestatus, avg(l_discount) as avg_disc
from tpch_sf1.lineitem group by l_returnflag, l_linestatus
```

| | |
|---|---|
| **Source** (Snowflake) | `0.04998530 (decimal, scale 8)` |
| **Copy** (DuckDB on the Iceberg copy) | `0.04998529583845148 (float)` |

**Why.** In Snowflake, the average of a decimal stays decimal, rounded to 8 decimal places; DuckDB computes it as a float. The relative difference is about 7e-8, above Canot's default tolerance (1e-9).

**What to do.** Decide on a tolerance: `decimal_abs` or `float_rel` in the tolerances file (`canot init-tolerances`), or round in the query. An accounting amount deserves a tolerance written down and signed off by the business.

## Nanoseconds do not survive Iceberg v2

*Measured on 2026-10-17. DuckDB 1.5.6; pyiceberg 0.12; Canot 0.1.0 (`canot demo`).*

```sql
select min(scanned_at), max(scanned_at) from distrib.tickets
```

| | |
|---|---|
| **Source** (Any engine with nanosecond timestamps (for example DuckDB TIMESTAMP_NS, Snowflake TIMESTAMP(9))) | `2026-03-01 08:00:10.000000311` |
| **Copy** (Apache Iceberg v2) | `2026-03-01 08:00:10` |

**Why.** Iceberg v2 timestamps stop at the microsecond: the copy rounds off anything below. This is not corruption, it is a limit of the destination type. The Iceberg v3 specification defines a nanosecond timestamp; support for it depends on each engine and each version.

**What to do.** Decide whether sub-microsecond precision matters (sensor logs, trading, traceability). If it does, store the timestamp as text or as an integer (nanoseconds since 1970) alongside it, or target an Iceberg v3-compatible engine.

## Snowflake TIMESTAMP_TZ: the copy keeps the instant, not the zone, so a date derived from it can shift by a day

*Measured on 2026-10-19. Snowflake (trial account, test bench dbt project); DuckDB 1.5.6; pyiceberg 0.12; Canot 0.3.5 (`canot drill dbt://`).*

```sql
select max(cast(order_ts as date)) as last_order_date from RAW.ORDERS  -- order_ts: TIMESTAMP_TZ, America/Los_Angeles
```

| | |
|---|---|
| **Source** (Snowflake (the date the dbt model stored, in the zone of each value)) | `2025-08-22` |
| **Copy** (DuckDB on the Iceberg copy (session zone Europe/Paris)) | `2025-08-23` |

**Why.** Iceberg stores a timestamp with time zone as an instant in UTC: the instant is kept, the zone of each value is not (canot drill logs the conversion as lossy). Snowflake casts a TIMESTAMP_TZ to a date in the zone stored with the value; on the copy the cast happens in the session zone of the engine. An order placed in the evening in Los Angeles falls on the next day in Paris. On the test bench dbt project (canot drill dbt://), the copy of the 8 tables is identical, and the 3 marts that derive a date from ORDER_TS differ: dbt_daily_revenue has 14,423 rows instead of 14,399, and the latest order date moves from 2025-08-22 to 2025-08-23.

**What to do.** Decide which zone the business dates are in, and write it into the pipeline on the destination side: SET TimeZone in DuckDB, or convert explicitly before the cast (timezone('America/Los_Angeles', order_ts)). If the zone of each value matters, copy it as a separate column before leaving.

## To be measured

Not measured yet: draw no conclusion from it.

- **Time zones (BigQuery)**: BigQuery TIMESTAMP (UTC) and DATETIME (no zone): what the copy and a date derived from them return
- **Null ordering**: Each engine puts NULLs first or last by default: an ORDER BY can return a different order.
- **Case and collation**: Text comparisons and sorting depend on the platform's collation.
- **Semi-structured data**: VARIANT (Snowflake), JSON (BigQuery), STRUCT and ARRAY: text form and key order.
- **Large decimals**: Snowflake NUMBER(38), BigQuery BIGNUMERIC: how far is precision preserved?
- **Geography**: GEOGRAPHY / GEOMETRY: serialization format on the destination side.

## Suggest a discrepancy

Open an issue with: the query or the type, the result from each engine, the versions, the date. A discrepancy without a reproducible result does not enter the catalogue.
