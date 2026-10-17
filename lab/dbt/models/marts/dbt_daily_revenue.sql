select order_date, country, status, count(*) as orders, sum(total) as revenue
from {{ ref('dbt_fct_orders') }} group by order_date, country, status
