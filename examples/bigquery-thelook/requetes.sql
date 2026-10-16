select status, count(*) as n from `bigquery-public-data.thelook_ecommerce.orders` group by status order by n desc;

select p.category, round(sum(oi.sale_price), 2) as revenue, count(*) as items
from `bigquery-public-data.thelook_ecommerce.order_items` oi
join `bigquery-public-data.thelook_ecommerce.products` p on p.id = oi.product_id
group by p.category order by revenue desc limit 15;

select date_trunc(date(created_at), month) as m, count(*) as orders
from `bigquery-public-data.thelook_ecommerce.orders` group by m order by m;

select country, count(*) as users, avg(age) as avg_age
from `bigquery-public-data.thelook_ecommerce.users` group by country order by users desc;
