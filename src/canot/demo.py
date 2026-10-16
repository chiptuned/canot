"""Synthetic data that exercises the usual traps (decimals, timestamps, NULLs, JSON, unicode)."""
from __future__ import annotations

import duckdb


def make_demo(path: str, n: int = 20_000) -> None:
    con = duckdb.connect(path)
    con.execute("create schema if not exists shop")
    con.execute(f"""
        create or replace table shop.customers as
        select i as id, 'client_' || i as name,
               case when i % 7 = 0 then null else 'ville_' || (i % 50) end as city,
               case when i % 11 = 0 then 'Zoé ☃ ' || i else 'plain ' || i end as note
        from range(1, {n // 10 + 1}) t(i)""")
    con.execute(f"""
        create or replace table shop.orders as
        select i as id, 1 + (i % {n // 10}) as customer_id,
               cast((i * 37 % 100000) / 100.0 as decimal(18,2)) as total,
               (i * 0.137)::double as score,
               timestamp '2026-01-01 00:00:00' + to_seconds(i * 997) + to_microseconds(i % 1000000) as created_at,
               case when i % 13 = 0 then null else date '2026-01-01' + (i % 300)::int end as ship_date,
               '{{"k":' || i || ',"tags":["a","b"]}}' as payload
        from range(1, {n + 1}) t(i)""")
    con.execute("""
        create or replace table shop.empty_table (id integer, label varchar)""")
    con.close()


DEMO_QUERIES = """\
select city, count(*) as n, sum(o.total) as revenue
from shop.orders o join shop.customers c on c.id = o.customer_id
group by city order by revenue desc nulls last;

select date_trunc('month', created_at) as m, count(*) as n, avg(score) as avg_score
from shop.orders group by 1 order by 1;

select customer_id, max(total) as biggest
from shop.orders where ship_date is not null group by 1 order by biggest desc limit 20;

select * from shop.orders order by ship_date limit 50;
"""
