"""BigQuery test bench: a deliberately complex warehouse, created in a personal project (free tier).
Everything is generated in SQL (GENERATE_ARRAY): no files, negligible cost.
Usage: python lab/bigquery_lab.py PROJECT [--drop]"""
import sys

from google.cloud import bigquery

project = sys.argv[1]
LOC = "EU"
c = bigquery.Client(project=project, location=LOC)


def run(sql, labels=None, desc=""):
    cfg = bigquery.QueryJobConfig(labels=labels or {"tool": "lab"})
    job = c.query(sql, job_config=cfg)
    job.result()
    print(f"ok  {desc or sql.strip().splitlines()[0][:70]}  ({(job.total_bytes_billed or 0) / 1e6:.0f} MB billed)")


if "--drop" in sys.argv:
    for ds in ("lab_raw", "lab_stg", "lab_mart", "lab_ml", "lab_ext"):
        c.delete_dataset(f"{project}.{ds}", delete_contents=True, not_found_ok=True)
    print("deleted")
    sys.exit(0)

for ds, desc in (("lab_raw", "Ingested raw data"), ("lab_stg", "Staging layer (views)"), ("lab_mart", "Analytics model"),
                 ("lab_ml", "BigQuery ML models"), ("lab_ext", "External sources")):
    d = bigquery.Dataset(f"{project}.{ds}")
    d.location = LOC
    d.description = desc
    d.labels = {"env": "lab", "owner": "canot", "domain": ds.split("_")[1]}
    c.create_dataset(d, exists_ok=True)
print("datasets created")

P = f"`{project}`"
EMAIL = c.get_service_account_email() if False else __import__("subprocess").run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True).stdout.strip()
# ---- raw: deterministically generated tables
run(f"""create or replace table {P}.lab_raw.customers
options(description="Customers (made-up data)", labels=[("domain","crm"),("pii","true")]) as
select i as customer_id, concat('client_', cast(i as string)) as full_name,
       concat('client_', cast(i as string), '@exemple.test') as email,
       ['FR','DE','ES','IT','NL','BE'][offset(mod(i,6))] as country,
       timestamp_add(timestamp '2023-01-01', interval mod(i*37, 900) day) as signed_up_at,
       if(mod(i,17)=0, null, ['pro','perso','asso'][offset(mod(i,3))]) as segment
from unnest(generate_array(1, 20000)) as i""", desc="lab_raw.customers")
run(f"""create or replace table {P}.lab_raw.products as
select i as product_id, concat('Produit ', cast(i as string)) as label,
       ['frais','epicerie','boissons','hygiene','bazar'][offset(mod(i,5))] as category,
       cast(round(0.5 + mod(i*13, 2500)/100.0, 2) as numeric) as unit_price,
       cast(mod(i*7, 400)/10.0 as bignumeric) as weight_kg
from unnest(generate_array(1, 1500)) as i""", desc="lab_raw.products (NUMERIC, BIGNUMERIC)")
run(f"""create or replace table {P}.lab_raw.orders as
select i as order_id, 1 + mod(i*11, 20000) as customer_id,
       timestamp_add(timestamp '2024-01-01', interval mod(i*53, 600*24*60) minute) as order_ts,
       cast(round(5 + mod(i*97, 50000)/100.0, 2) as numeric) as total,
       ['card','transfer','paypal'][offset(mod(i,3))] as payment_method,
       ['open','paid','shipped','returned'][offset(mod(i,4))] as status
from unnest(generate_array(1, 150000)) as i""", desc="lab_raw.orders")
run(f"""create or replace table {P}.lab_raw.order_lines as
select i as line_id, 1 + div(i, 3) as order_id, 1 + mod(i*5, 1500) as product_id, 1 + mod(i, 4) as qty
from unnest(generate_array(1, 450000)) as i""", desc="lab_raw.order_lines")
run(f"""create or replace table {P}.lab_raw.events
options(description="Application events: STRUCT, ARRAY, JSON") as
select i as event_id, timestamp_add(timestamp '2024-06-01', interval i second) as ts,
       struct(concat('u', cast(mod(i,5000) as string)) as user_id, ['web','ios','android'][offset(mod(i,3))] as device) as actor,
       [struct('page' as k, concat('/p/', cast(mod(i,40) as string)) as v), struct('ref' as k, 'direct' as v)] as props,
       parse_json(concat('{{"cart":', cast(mod(i,9) as string), ',"flags":["a","b"],"ab":{{"variant":"', ['A','B'][offset(mod(i,2))], '"}}}}')) as payload,
       st_geogpoint(2.0 + mod(i,100)/100.0, 48.5 + mod(i,100)/100.0) as geo
from unnest(generate_array(1, 200000)) as i""", desc="lab_raw.events (STRUCT, ARRAY, JSON, GEOGRAPHY)")

# ---- staging: views
run(f"create or replace view {P}.lab_stg.stg_customers as select customer_id, lower(email) as email, country, coalesce(segment,'inconnu') as segment, signed_up_at from {P}.lab_raw.customers", desc="view stg_customers")
run(f"create or replace view {P}.lab_stg.stg_orders as select order_id, customer_id, order_ts, date(order_ts) as order_date, total, status, payment_method from {P}.lab_raw.orders", desc="view stg_orders")

# ---- routines
run(f"create or replace function {P}.lab_mart.fx_to_cents(x numeric) returns int64 as (cast(round(x*100) as int64))", desc="UDF SQL")
run(f"""create or replace function {P}.lab_mart.clean_name(s string) returns string language js as r'''
  return s == null ? null : s.toLowerCase().replace(/[^a-z0-9]+/g, '_');
'''""", desc="UDF JavaScript")
run(f"""create or replace table function {P}.lab_mart.orders_of_country(c string) as
select o.* from {P}.lab_stg.stg_orders o join {P}.lab_stg.stg_customers u using (customer_id) where u.country = c""", desc="table function")
run(f"""create or replace procedure {P}.lab_mart.sp_refresh_mart()
begin
  create or replace table {P}.lab_mart.dim_customers as select * from {P}.lab_stg.stg_customers;
end""", desc="stored procedure")

# ---- mart: partitioned + clustered, materialized view, snapshot, clone
run(f"""create or replace table {P}.lab_mart.fct_orders
partition by order_date cluster by customer_id, status
options(description="Order facts", labels=[("layer","mart")]) as
select o.order_id, o.customer_id, o.order_ts, date_sub(current_date(), interval mod(o.order_id, 55) day) as order_date, o.total, o.status, c.country, c.segment,
       {P}.lab_mart.fx_to_cents(o.total) as total_cents
from {P}.lab_stg.stg_orders o join {P}.lab_stg.stg_customers c using (customer_id)""", desc="fct_orders (partitioned, clustered)")
run(f"create or replace table {P}.lab_mart.dim_customers as select * from {P}.lab_stg.stg_customers", desc="dim_customers")
run(f"""create or replace materialized view {P}.lab_mart.mv_daily_revenue as
select order_date, status, count(*) as orders, sum(total) as revenue from {P}.lab_mart.fct_orders group by order_date, status""", desc="materialized view")
# without billing, partitions older than 60 days expire: fct_orders dates are therefore relative to today
for t in ("fct_orders_snapshot", "fct_orders_dev"):
    c.delete_table(f"{project}.lab_mart.{t}", not_found_ok=True)
run(f"create snapshot table if not exists {P}.lab_mart.fct_orders_snapshot clone {P}.lab_mart.fct_orders", desc="snapshot")
run(f"create table if not exists {P}.lab_mart.fct_orders_dev clone {P}.lab_mart.fct_orders", desc="clone")
run(f"""create or replace row access policy only_owner on {P}.lab_mart.fct_orders
grant to ('user:{EMAIL}') filter using (status <> 'returned')""", desc="row access policy (partial filter: a copy made under this identity would be incomplete)")
run(f"""create or replace external table {P}.lab_ext.us_states
options(format='CSV', uris=['gs://cloud-samples-data/bigquery/us-states/us-states.csv'], skip_leading_rows=1)""", desc="external table (public GCS)")

# ---- ML
try:
    run(f"""create or replace model {P}.lab_ml.churn_proxy options(model_type='logistic_reg', input_label_cols=['returned']) as
    select total, country, ifnull(segment, 'inconnu') as segment, status = 'returned' as returned from {P}.lab_raw.orders o join {P}.lab_raw.customers using (customer_id) where date(order_ts) < '2024-10-01'""", desc="BigQuery ML model")
except Exception as e:
    print("ML model not created:", str(e)[:120])

# ---- realistic workload: several applications, labels and frequencies
queries = [
    ("looker", "finance", f"select country, sum(total) as ca from {P}.lab_mart.fct_orders where order_date >= '2024-06-01' group by country order by ca desc"),
    ("looker", "finance", f"select order_date, revenue from {P}.lab_mart.mv_daily_revenue where status = 'paid' order by order_date"),
    ("looker", "marketing", f"select segment, count(distinct customer_id) as n from {P}.lab_mart.fct_orders group by segment"),
    ("dbt", "data", f"select count(*) from {P}.lab_stg.stg_orders"),
    ("dbt", "data", f"select count(*) from {P}.lab_stg.stg_customers"),
    ("notebook", "data-science", f"select json_value(payload, '$.ab.variant') as v, count(*) as n from {P}.lab_raw.events group by v"),
    ("notebook", "data-science", f"select actor.device, count(*) as n, st_x(st_centroid_agg(geo)) as lon from {P}.lab_raw.events group by 1"),
    ("airflow", "ops", f"select {P}.lab_mart.clean_name(full_name) as n, count(*) from {P}.lab_raw.customers group by n limit 20"),
    ("airflow", "ops", f"select * from {P}.lab_mart.orders_of_country('FR') limit 100"),
]
for _ in range(3):
    for app, team, q in queries:
        run(q, labels={"app": app, "team": team}, desc=f"workload {app}/{team}")
print("BigQuery test bench ready")
