"""Corpus de rejouabilité : catégories consignées d'avance (spec §3 et §6, « Rejouabilité : catégories identiques à celles du corpus »).

Chaque entrée : (id, dialecte, sql, catégorie attendue). Les écarts constatés avec la spec sont notés en commentaire
(sqlglot 30.22.0, duckdb 1.5.6). Le squelette (colonnes typées) est celui que COLUMNS rendrait sur le banc de test.
"""
CANARI = "canari-7f3a@exemple.test"

SNOWFLAKE_TABLES = [
    {"base": "ANALYTICS", "schema": "RAW", "nom": "ORDERS", "colonnes": [
        {"nom": "ORDER_ID", "type": "NUMBER(38,0)"}, {"nom": "CUSTOMER_ID", "type": "NUMBER(38,0)"},
        {"nom": "AMOUNT", "type": "NUMBER(12,2)"}, {"nom": "STATUS", "type": "VARCHAR"},
        {"nom": "CREATED_AT", "type": "TIMESTAMP_NTZ"}, {"nom": "UPDATED_AT", "type": "TIMESTAMP_LTZ"},
        {"nom": "PAYLOAD", "type": "VARIANT"}]},
    {"base": "ANALYTICS", "schema": "RAW", "nom": "CUSTOMERS", "colonnes": [
        {"nom": "CUSTOMER_ID", "type": "NUMBER(38,0)"}, {"nom": "EMAIL", "type": "VARCHAR"}, {"nom": "COUNTRY", "type": "VARCHAR"},
        {"nom": "ATTRS", "type": "VARIANT"}, {"nom": "LOCATION", "type": "GEOGRAPHY"},
        {"nom": "CREATED_AT", "type": "TIMESTAMP_NTZ"}, {"nom": "IS_ACTIVE", "type": "BOOLEAN"}]},
    {"base": "ANALYTICS", "schema": "RAW", "nom": "EVENTS", "colonnes": [
        {"nom": "EVENT_ID", "type": "VARCHAR"}, {"nom": "USER_ID", "type": "NUMBER"}, {"nom": "EVENT_TYPE", "type": "VARCHAR"},
        {"nom": "TS", "type": "TIMESTAMP_NTZ"}, {"nom": "PROPS", "type": "VARIANT"}, {"nom": "TAGS", "type": "ARRAY"}]},
    {"base": "ANALYTICS", "schema": "STG", "nom": "STG_ORDERS", "colonnes": [
        {"nom": "ORDER_ID", "type": "NUMBER"}, {"nom": "CUSTOMER_ID", "type": "NUMBER"}, {"nom": "AMOUNT", "type": "NUMBER(12,2)"},
        {"nom": "STATUS", "type": "VARCHAR"}, {"nom": "ORDER_DATE", "type": "DATE"}]},
    {"base": "ANALYTICS", "schema": "MART", "nom": "DAILY_SALES", "colonnes": [
        {"nom": "DAY", "type": "DATE"}, {"nom": "COUNTRY", "type": "VARCHAR"}, {"nom": "REVENUE", "type": "NUMBER(18,2)"},
        {"nom": "ORDERS", "type": "NUMBER"}]},
    {"base": "ANALYTICS", "schema": "MART", "nom": "FCT_ORDERS", "colonnes": [
        {"nom": "ORDER_ID", "type": "NUMBER"}, {"nom": "CUSTOMER_ID", "type": "NUMBER"}, {"nom": "COUNTRY", "type": "VARCHAR"},
        {"nom": "AMOUNT", "type": "NUMBER(12,2)"}, {"nom": "ORDER_DATE", "type": "DATE"}]},
]
SNOWFLAKE_FUNCTIONS = ["ANALYTICS.MART.CLEAN_EMAIL(VARCHAR)", "ANALYTICS.MART.SCORE_JS(FLOAT)"]

BIGQUERY_TABLES = [
    {"base": "acme-data", "schema": "sales", "nom": "orders", "colonnes": [
        {"nom": "order_id", "type": "INT64"}, {"nom": "customer_id", "type": "INT64"}, {"nom": "amount", "type": "NUMERIC"},
        {"nom": "status", "type": "STRING"}, {"nom": "created_at", "type": "TIMESTAMP"},
        {"nom": "items", "type": "ARRAY<STRUCT<sku STRING, qty INT64, price NUMERIC>>"}, {"nom": "meta", "type": "JSON"}]},
    {"base": "acme-data", "schema": "sales", "nom": "customers", "colonnes": [
        {"nom": "customer_id", "type": "INT64"}, {"nom": "email", "type": "STRING"}, {"nom": "country", "type": "STRING"},
        {"nom": "address", "type": "STRUCT<city STRING, zip STRING>"}, {"nom": "signup_date", "type": "DATE"},
        {"nom": "location", "type": "GEOGRAPHY"}]},
    {"base": "acme-data", "schema": "analytics", "nom": "events_20240101", "colonnes": [
        {"nom": "event_name", "type": "STRING"}, {"nom": "user_id", "type": "STRING"}, {"nom": "event_ts", "type": "TIMESTAMP"},
        {"nom": "params", "type": "ARRAY<STRUCT<key STRING, value STRING>>"}]},
    {"base": "acme-data", "schema": "analytics", "nom": "events_20240102", "colonnes": [
        {"nom": "event_name", "type": "STRING"}, {"nom": "user_id", "type": "STRING"}, {"nom": "event_ts", "type": "TIMESTAMP"},
        {"nom": "params", "type": "ARRAY<STRUCT<key STRING, value STRING>>"}]},
    {"base": "acme-data", "schema": "analytics", "nom": "daily", "colonnes": [
        {"nom": "day", "type": "DATE"}, {"nom": "country", "type": "STRING"}, {"nom": "revenue", "type": "NUMERIC"}]},
]
BIGQUERY_FUNCTIONS = ["acme-data.udfs.normalize_sku"]

DBT_SF = '/* {"app": "dbt", "dbt_version": "1.8.7", "profile_name": "acme", "target_name": "prod", "node_id": "model.acme.fct_orders"} */\n'
DBT_BQ = '/* {"app": "dbt", "dbt_version": "1.9.1", "profile_name": "acme", "target_name": "prod", "node_id": "model.acme.daily"} */\n'

SNOWFLAKE = [
    # C0 : traduit et accepté par DuckDB
    ("S01", "select country, count(*) as n from raw.customers where is_active = true group by 1 order by 2 desc", "C0"),
    ("S02", "select * from raw.orders qualify row_number() over (partition by customer_id order by created_at desc) = 1", "C0"),
    ("S03", "select order_id, iff(amount > 100, 'big', 'small') as size, nvl(status, 'n/a') as status from raw.orders "
            "where created_at >= dateadd(day, -7, current_date())", "C0"),
    ("S04", "select payload:customer.name::string as name, payload:channel::string as channel from raw.orders", "C0"),
    ("S05", f"select customer_id, email from raw.customers where email in ('{CANARI}', 'b@exemple.test') and country = 'FR'", "C0"),
    ("S06", "select count(*) from raw.customers where email ilike '%@exemple.test'", "C0"),
    ("S07", "with o as (select customer_id, sum(amount) as total from raw.orders group by 1) "
            "select c.country, sum(o.total) from o join raw.customers c on c.customer_id = o.customer_id group by 1", "C0"),
    ("S08", "select order_id, amount from raw.orders where status = 'paid' union all "
            "select order_id, amount from stg.stg_orders where status = 'paid'", "C0"),
    ("S09", "select day, revenue, sum(revenue) over (order by day rows between 6 preceding and current row) as r7 from mart.daily_sales", "C0"),
    ("S10", "create or replace table mart.daily_sales as select o.created_at::date as day, c.country, sum(o.amount) as revenue, "
            "count(*) as orders from raw.orders o join raw.customers c on c.customer_id = o.customer_id group by 1, 2", "C0"),
    ("S11", "create or replace view mart.v_kpi as select day, sum(revenue) as revenue from mart.daily_sales group by day", "C0"),
    ("S12", "insert into mart.daily_sales select order_date, 'FR', sum(amount), count(*) from stg.stg_orders group by 1", "C0"),
    ("S13", DBT_SF + "create or replace transient table analytics.mart.fct_orders as (\n  select o.order_id, o.customer_id, c.country, "
            "o.amount, o.created_at::date as order_date\n  from analytics.raw.orders o left join analytics.raw.customers c "
            "using (customer_id)\n)", "C0"),
    ("S14", "select order_id, amount from raw.orders where customer_id = ? and status = ?", "C0"),  # liaison client : pas une variable
    ("S15", "select customer_id, listagg(status, ',') within group (order by created_at) from raw.orders group by 1", "C0"),
    ("S16", "select * from (select country, day, revenue from mart.daily_sales) pivot(sum(revenue) for country in ('FR', 'DE', 'IT')) as p", "C0"),
    ("S17", "select * from raw.events sample (10)", "C0"),
    ("S18", "select seq4() as n from table(generator(rowcount => 100))", "C0"),
    ("S19", "select count(*) from analytics.raw.orders", "C0"),
    ("S20", "select count(*) from orders where status = 'shipped'", "C0"),
    ("S21", "select customer_id, object_construct('n', count(*), 'total', sum(amount)) as o from raw.orders group by 1", "C0"),
    ("S22", "select try_to_number(status) as s, to_varchar(amount) as a from raw.orders", "C0"),
    ("S23", "select order_id, datediff('day', created_at, current_timestamp()) as age_days from raw.orders", "C0"),
    ("S24", "select date_trunc('month', created_at) as m, sum(amount) from raw.orders "
            "where created_at between '2026-01-01' and '2026-06-30' group by 1", "C0"),
    ("S25", "select c.customer_id, x.last_amount from raw.customers c, lateral (select amount as last_amount from raw.orders o "
            "where o.customer_id = c.customer_id order by created_at desc limit 1) x", "C0"),
    ("S26", "select o.order_id from raw.orders o where exists (select 1 from raw.customers c where c.customer_id = o.customer_id "
            "and c.country not in ('US', 'CA'))", "C0"),
    # C3 : traduction impossible
    ("S27", "select to_char(created_at, 'YYYY-MM') as month, sum(amount) from raw.orders group by 1", "C3"),
    ("S28", "select to_char(amount, '999G999D99') from raw.orders", "C3"),
    # C4 : erreur d'analyse DuckDB
    ("S29", "select o.order_id, f.value:sku::string as sku from raw.orders o, lateral flatten(input => o.payload:items) f", "C4"),
    ("S30", "select e.event_id, t.value::string as tag from raw.events e, table(flatten(e.tags)) t", "C4"),
    ("S31", "select * from table(result_scan(last_query_id())) where \"rows_inserted\" > 0", "C4"),
    ("S32", "select $1 as raw_line, metadata$filename as file from @raw.landing/orders/ (file_format => 'raw.csv_format')", "C4"),
    ("S33", "select * from raw.orders match_recognize(partition by customer_id order by created_at measures first(amount) as first_amount "
            "one row per match pattern (a b+) define b as amount > prev(amount))", "C4"),
    ("S34", "select * from raw.orders before(statement => '01b2c3d4-0000-1111-0000-000000000001')", "C4"),
    ("S35", "select * from raw.orders at(offset => -3600)", "C4"),
    # C5 : erreur de liaison
    ("S36", "select analytics.mart.clean_email(email) as e from raw.customers", "C5"),
    ("S37", "select clean_email(email) from raw.customers", "C5"),
    ("S38", "select customer_id, st_distance(location, st_makepoint(2.35, 48.85)) as d from raw.customers", "C5"),
    ("S39", "select current_warehouse(), current_account()", "C5"),
    ("S40", "select metadata$action, metadata$isupdate, order_id from raw.orders_stream where metadata$action = 'INSERT'", "C5"),
    ("S41", "select * from raw.legacy_orders_2019", "C5"),
    ("S42", "select hll(customer_id) from raw.orders", "C5"),
    ("S43", "select system$clustering_information('raw.orders')", "C5"),
    # spec : AT -> C4 ; DuckDB 1.5.6 analyse AT (TIMESTAMP => …) et refuse à la liaison (« Catalog type does not support time travel »)
    ("S44", "select * from raw.orders at(timestamp => '2026-10-01 00:00:00'::timestamp_ltz)", "C5"),
    # C6 : variable de session
    ("S45", "select * from raw.orders where status = $status_filter", "C6"),
    ("S46", "select $run_date::date as d, count(*) from raw.orders where created_at::date = $run_date group by 1", "C6"),
    # C7 : DDL, COPY, CLONE, MERGE, DML (compté à part)
    ("S47", DBT_SF + "merge into analytics.mart.fct_orders as t using (select * from analytics.stg.stg_orders "
            "where order_date >= '2026-10-01') as s on t.order_id = s.order_id when matched then update set t.amount = s.amount "
            "when not matched then insert (order_id, customer_id, amount, order_date) values (s.order_id, s.customer_id, s.amount, s.order_date)", "C7"),
    ("S48", "copy into raw.orders from @raw.landing/orders/ file_format = (type = csv skip_header = 1) on_error = 'continue'", "C7"),
    ("S49", f"copy into 's3://acme-exports/nightly/{CANARI}/orders_' from (select * from mart.fct_orders) "
            "storage_integration = s3_int file_format = (type = parquet) header = true", "C7"),
    ("S50", "copy into @raw.int_stage/daily/ from (select * from mart.daily_sales) file_format = (type = parquet)", "C7"),
    ("S51", "create table mart.fct_orders_bkp clone mart.fct_orders", "C7"),
    ("S52", "create table if not exists raw.audit (id number autoincrement, at timestamp_ntz default current_timestamp(), who varchar)", "C7"),
    ("S53", "drop table if exists raw.tmp_dropped", "C7"),
    ("S54", "alter table raw.orders add column channel varchar", "C7"),
    ("S55", "delete from raw.orders where created_at < '2020-01-01'", "C7"),
    ("S56", f"update raw.customers set email = '{CANARI}' where customer_id = 42", "C7"),
    ("S57", "truncate table stg.stg_orders", "C7"),
    ("S58", f"insert into raw.customers (customer_id, email) values (1, '{CANARI}')", "C7"),
    ("S59", "create or replace function analytics.mart.clean_email(e varchar) returns varchar as 'lower(trim(e))'", "C7"),
    # C2 : commandes (verbe seul conservé quand sqlglot rend une Command)
    ("S60", f"call analytics.ops.sp_refresh('{CANARI}')", "C2"),
    ("S61", "execute immediate 'select 1'", "C2"),
    ("S62", "show tables in schema analytics.raw", "C2"),
    ("S63", "use warehouse transform_wh", "C2"),
    ("S64", "alter session set timezone = 'UTC'", "C2"),
    ("S65", "begin", "C2"),
    ("S66", "grant select on table raw.orders to role analyst", "C2"),
    ("S67", "create or replace task ops.t_daily warehouse = transform_wh schedule = 'USING CRON 0 6 * * * UTC' as call ops.sp_daily()", "C2"),
    # C1 : non analysable (texte tronqué par l'historique)
    ("S68", "select order_id, status from raw.orders where status in ('shipped', 'deliv", "C1"),
    ("S69", "select count(*) from raw.orders where (amount > 10 and status = 'x'", "C1"),
]

BIGQUERY = [
    ("B01", "select * except (meta, items) from `acme-data.sales.orders` where status = 'paid'", "C0"),
    ("B02", "select country, count(*) as n from `acme-data`.`sales`.`customers` group by country", "C0"),
    ("B03", "select o.order_id, i.sku, i.qty * i.price as line from `acme-data.sales.orders` o, unnest(o.items) as i", "C0"),
    ("B04", "select struct(order_id as id, amount as amt) as s from sales.orders", "C0"),
    ("B05", "select format_date('%Y-%m', date(created_at)) as m, countif(status = 'paid') as paid, "
            "safe_divide(sum(amount), count(*)) as avg_amount from `acme-data.sales.orders` group by 1", "C0"),
    ("B06", "select * from `acme-data.sales.orders` qualify row_number() over (partition by customer_id order by created_at desc) = 1", "C0"),
    ("B07", "select * from `acme-data.sales.orders` where customer_id = @customer_id and created_at >= @since", "C0"),  # paramètres liés
    ("B08", DBT_BQ + "create or replace table `acme-data`.`analytics`.`daily` as (\n  select date(o.created_at) as day, c.country, "
            "sum(o.amount) as revenue\n  from `acme-data`.`sales`.`orders` o join `acme-data`.`sales`.`customers` c using (customer_id)\n"
            "  group by 1, 2\n)", "C0"),
    ("B09", "select json_value(meta, '$.channel') as channel, count(*) from `acme-data.sales.orders` group by 1", "C0"),
    ("B10", "select c.address.city, count(*) from `acme-data.sales.customers` c group by 1", "C0"),
    ("B11", f"select customer_id from `acme-data.sales.customers` where email in ('{CANARI}', 'x@exemple.test')", "C0"),
    ("B12", "select * replace (upper(status) as status) from `acme-data.sales.orders`", "C0"),
    ("B13", "insert into `acme-data.analytics.daily` (day, country, revenue) select date(o.created_at), c.country, sum(o.amount) "
            "from `acme-data.sales.orders` o join `acme-data.sales.customers` c using (customer_id) group by 1, 2", "C0"),
    ("B14", "create or replace view `acme-data.analytics.v_paid` as select * from `acme-data.sales.orders` where status = 'paid'", "C0"),
    ("B15", "select day, country, revenue, avg(revenue) over (partition by country order by day rows between 6 preceding and current row) "
            "as avg7 from `acme-data.analytics.daily`", "C0"),
    ("B16", "select timestamp_trunc(created_at, month) as m, sum(amount) from sales.orders group by 1", "C0"),
    ("B17", "select customer_id, array_agg(order_id order by created_at desc limit 5) as last5 from `acme-data.sales.orders` group by 1", "C3"),
    ("B18", "select * from `acme-data.sales.orders` for system_time as of timestamp_sub(current_timestamp(), interval 1 hour)", "C4"),
    ("B19", "select * from ml.predict(model `acme-data.sales.churn_model`, (select * from `acme-data.sales.customers`))", "C4"),
    # spec : _TABLE_SUFFIX -> C4 ; DuckDB 1.5.6 analyse "events_*" comme un identifiant et refuse à la liaison (table absente)
    ("B20", "select event_name, count(*) from `acme-data.analytics.events_*` where _TABLE_SUFFIX between '20240101' and '20240131' group by 1", "C5"),
    ("B21", "select count(*) from `acme-data.analytics.daily` where _PARTITIONTIME >= timestamp('2026-01-01')", "C5"),
    ("B22", "select st_distance(location, st_geogpoint(2.35, 48.85)) from `acme-data.sales.customers`", "C5"),
    ("B23", "select farm_fingerprint(email) as h from `acme-data.sales.customers`", "C5"),
    ("B24", "select `acme-data.udfs.normalize_sku`(i.sku) from `acme-data.sales.orders` o, unnest(o.items) i", "C5"),
    ("B25", "select table_name from `acme-data.sales.INFORMATION_SCHEMA.TABLES`", "C7"),
    ("B26", "merge `acme-data.analytics.daily` t using (select date(created_at) as day, 'FR' as country, sum(amount) as revenue "
            "from `acme-data.sales.orders` group by 1) s on t.day = s.day and t.country = s.country "
            "when matched then update set revenue = s.revenue when not matched then insert row", "C7"),
    ("B27", f"export data options(uri='gs://acme-exports/{CANARI}/orders-*.csv', format='CSV', overwrite=true) as "
            "select * from `acme-data.sales.orders`", "C7"),
    ("B28", "create table `acme-data.sales.tmp_orders` (order_id int64, amount numeric)", "C7"),
    ("B29", "create or replace model `acme-data.sales.churn_model` options(model_type='logistic_reg') as select * from `acme-data.sales.customers`", "C7"),
    ("B30", "delete from `acme-data.sales.orders` where created_at < timestamp('2020-01-01')", "C7"),
    ("B31", "create table `acme-data.sales.orders_clone` clone `acme-data.sales.orders`", "C7"),
    ("B32", "truncate table `acme-data.sales.tmp_orders`", "C7"),
    ("B33", "declare cutoff date default current_date()", "C2"),
    ("B34", "call `acme-data.ops.refresh_daily`()", "C2"),
    ("B35", "create snapshot table `acme-data.sales.orders_snap` clone `acme-data.sales.orders`", "C2"),  # Command pour sqlglot
    ("B36", "select order_id from `acme-data.sales.orders` where status = 'pai", "C1"),
]

CORPUS = [("snowflake", *e) for e in SNOWFLAKE] + [("bigquery", *e) for e in BIGQUERY]

# Requêtes à secret : comptées puis jetées, jamais normalisées ni retenues
SECRETS = [
    f"create user analyst_bob password = '{CANARI}' default_role = analyst must_change_password = true",
    f"alter user analyst_bob set password = '{CANARI}'",
    f"create or replace stage ext.s3_public url = 's3://acme-public/data/' credentials = (aws_key_id = 'AKIAX' aws_secret_key = '{CANARI}')",
    f"create secret ops.gh_token type = password username = 'bot' password = '{CANARI}'",
    f"copy into 's3://acme-exports/x/' from raw.orders credentials = (aws_key_id = 'k' aws_secret_key = '{CANARI}')",
]
