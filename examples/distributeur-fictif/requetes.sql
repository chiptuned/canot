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
