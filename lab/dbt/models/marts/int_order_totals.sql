select l.order_id, sum(l.qty * p.unit_price) as basket_value, sum(l.qty) as items
from {{ ref('stg_order_lines') }} l join {{ ref('stg_products') }} p using (product_id)
group by l.order_id
