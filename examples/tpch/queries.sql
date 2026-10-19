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
