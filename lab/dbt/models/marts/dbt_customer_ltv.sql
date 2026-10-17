select customer_id, count(*) as orders, sum(total) as lifetime_value, min(order_date) as first_order, max(order_date) as last_order
from {{ ref('dbt_fct_orders') }} where status <> 'returned' group by customer_id
