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


def make_retail(path: str, tickets: int = 50_000) -> None:
    """Fictional grocery retailer ("Maison Marché"): invented data only. Includes nested lines
    (ARRAY<STRUCT>, as BigQuery exports them), decimals, NULLs, accents and a JSON-as-text column."""
    con = duckdb.connect(path)
    con.execute("create schema if not exists distrib")
    con.execute("""
        create or replace table distrib.stores as
        select i as store_id, 'Magasin ' || i as name,
               (['hyper','super','proxi','drive'])[1 + i % 4] as format,
               (['Île-de-France','Occitanie','Bretagne','Hauts-de-France','Auvergne-Rhône-Alpes'])[1 + i % 5] as region,
               date '2008-01-01' + (i * 37 % 6000)::int as opened_on
        from range(1, 301) t(i)""")
    con.execute("""
        create or replace table distrib.products as
        select i as product_id, 'Produit ' || i as label,
               (['frais','épicerie','boissons','hygiène','bazar'])[1 + i % 5] as category,
               cast(0.5 + (i * 13 % 2500) / 100.0 as decimal(10,2)) as unit_price,
               case when i % 9 = 0 then null else 'marque_' || (i % 40) end as brand
        from range(1, 2001) t(i)""")
    con.execute(f"""
        create or replace table distrib.loyalty_customers as
        select i as customer_id, case when i % 5 = 0 then null else 'ville_' || (i % 80) end as city,
               timestamp '2022-01-01' + to_days((i % 1400)::int) as joined_at,
               '{{"opt_in":' || (i % 2 = 0) || ',"segment":"s' || (i % 6) || '"}}' as prefs
        from range(1, 8001) t(i)""")
    con.execute(f"""
        create or replace table distrib.tickets as
        select i as ticket_id, 1 + (i * 7 % 300) as store_id,
               case when i % 4 = 0 then null else 1 + (i * 11 % 8000) end as customer_id,
               timestamp '2026-03-01 08:00:00' + to_seconds((i * 53) % 2500000) as sold_at,
               make_timestamp_ns(epoch_ns(timestamp '2026-03-01 08:00:00') + ((i * 53) % 2500000) * 1000000000 + (i % 997)) as scanned_at,
               cast(((i * 97) % 20000) / 100.0 + 1 as decimal(12,2)) as total,
               [struct_pack(product_id := 1 + (i * 3 % 2000), qty := 1 + i % 4),
                struct_pack(product_id := 1 + (i * 5 % 2000), qty := 1 + i % 2)] as lines
        from range(1, {tickets + 1}) t(i)""")
    con.close()


RETAIL_QUERIES = """\
select s.region, s.format, count(*) as tickets, sum(t.total) as revenue, avg(t.total) as basket
from distrib.tickets t join distrib.stores s using (store_id)
group by 1, 2 order by revenue desc;

select date_trunc('week', sold_at) as week, count(*) as tickets, sum(total) as revenue
from distrib.tickets group by 1 order by 1;

select c.city, count(*) as tickets, sum(t.total) as revenue
from distrib.tickets t join distrib.loyalty_customers c using (customer_id)
group by 1 order by revenue desc nulls last limit 30;

select p.category, sum(l.qty * p.unit_price) as revenue
from distrib.tickets t, unnest(t.lines) as u(l) join distrib.products p on p.product_id = l.product_id
group by 1 order by 2 desc;
"""


TPCH_QUERIES = """\
select l_returnflag, l_linestatus, sum(l_quantity) as sum_qty, sum(l_extendedprice) as sum_base_price,
       sum(l_extendedprice * (1 - l_discount)) as sum_disc_price, avg(l_discount) as avg_disc, count(*) as n
from tpch.lineitem where l_shipdate <= date '1998-12-01' - interval '90' day
group by 1, 2 order by 1, 2;

select l_orderkey, sum(l_extendedprice * (1 - l_discount)) as revenue, o_orderdate
from tpch.customer, tpch.orders, tpch.lineitem
where c_mktsegment = 'BUILDING' and c_custkey = o_custkey and l_orderkey = o_orderkey
  and o_orderdate < date '1995-03-15' and l_shipdate > date '1995-03-15'
group by l_orderkey, o_orderdate order by revenue desc, o_orderdate limit 10;

select sum(l_extendedprice * l_discount) as revenue from tpch.lineitem
where l_shipdate >= date '1994-01-01' and l_shipdate < date '1995-01-01' and l_discount between 0.05 and 0.07 and l_quantity < 24;

select * from tpch.lineitem order by l_orderkey, l_linenumber limit 50;
"""


def make_tpch(path: str, sf: float = 0.1) -> None:
    """TPC-H (the reference dataset of database benchmarks), generated locally by the DuckDB tpch extension.
    Canot adds a nanosecond timestamp column to LINEITEM, to show what Iceberg v2 does not keep."""
    con = duckdb.connect(path)
    con.execute("install tpch")
    con.execute("load tpch")
    con.execute("create schema if not exists tpch")
    con.execute("use tpch")
    con.execute(f"call dbgen(sf={sf})")
    con.execute("alter table lineitem add column l_loaded_at timestamp_ns")
    con.execute("update lineitem set l_loaded_at = make_timestamp_ns(epoch_ns(l_commitdate::timestamp) + l_orderkey * 1000 + l_linenumber)")
    con.close()
