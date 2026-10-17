select order_id, customer_id, order_ts, cast(order_ts as date) as order_date, total, status, payment_method
from {{ source('raw', 'orders') }}
