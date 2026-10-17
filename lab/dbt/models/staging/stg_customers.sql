select customer_id, lower(email) as email, country, coalesce(segment, 'inconnu') as segment, signed_up_at
from {{ source('raw', 'customers') }}
