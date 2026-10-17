select o.order_id, o.customer_id, o.order_date, o.total, o.status, c.country, c.segment, t.basket_value, t.items
from {{ ref('stg_orders') }} o
join {{ ref('stg_customers') }} c using (customer_id)
left join {{ ref('int_order_totals') }} t using (order_id)
