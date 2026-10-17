select product_id, label, category, unit_price from {{ source('raw', 'products') }}
