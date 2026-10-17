"""Banc de test Snowflake : un entrepôt volontairement complexe, dans une base dédiée (CANOT_LAB) d'un compte d'essai.
Prérequis : le script d'administration (rôle CANOT_LAB + utilisateur CANOT_LAB_USER) a été lancé une fois par un administrateur.
Chaque création est tentée séparément : ce qui exige une édition supérieure est signalé puis sauté.
Usage : SNOWFLAKE_PRIVATE_KEY_PATH=... python lab/snowflake_lab.py COMPTE [--drop]"""
import os
import sys

import snowflake.connector
from cryptography.hazmat.primitives import serialization

account = sys.argv[1]
with open(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], "rb") as f:
    key = serialization.load_pem_private_key(f.read(), password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
con = snowflake.connector.connect(account=account, user="CANOT_LAB_USER", private_key=der, role="CANOT_LAB", warehouse="CANOT_WH",
                                  session_parameters={"QUERY_TAG": "canot-lab"})
cur = con.cursor()
done, skipped = 0, []


def run(sql, desc="", tag=None):
    global done
    try:
        if tag:
            cur.execute(f"alter session set query_tag = '{tag}'")
        cur.execute(sql)
        done += 1
        print("ok  ", desc or sql.strip().splitlines()[0][:80])
        return True
    except Exception as e:
        skipped.append((desc or sql[:60], str(e).splitlines()[0][:140]))
        print("SAUTÉ", desc or sql[:60], "->", str(e).splitlines()[0][:110])
        return False


if "--drop" in sys.argv:
    run("drop database if exists CANOT_LAB", "suppression de la base")
    sys.exit(0)

run("create database if not exists CANOT_LAB", "base CANOT_LAB")
for sc in ("RAW", "STAGING", "MART", "DBT", "OPS", "SHARED"):
    run(f"create schema if not exists CANOT_LAB.{sc} with managed access" if sc == "MART" else f"create schema if not exists CANOT_LAB.{sc}", f"schéma {sc}")
run("use database CANOT_LAB")

# --- brut : types variés, volumes modestes
run("""create or replace table RAW.CUSTOMERS (customer_id number(38,0), full_name varchar, email varchar, country varchar(2),
       signed_up_at timestamp_ntz, segment varchar, profile variant) comment = 'Clients (inventés)' as
       select seq4()+1, 'client_'||(seq4()+1), 'client_'||(seq4()+1)||'@exemple.test', decode(uniform(0,5,random(1)),0,'FR',1,'DE',2,'ES',3,'IT',4,'NL','BE'),
              dateadd(day, uniform(0,900,random(2)), '2023-01-01'::timestamp_ntz), decode(uniform(0,2,random(3)),0,'pro',1,'perso','asso'),
              object_construct('plan','p'||uniform(1,3,random(4)),'tags',array_construct('a','b'))
       from table(generator(rowcount => 20000))""", "RAW.CUSTOMERS (VARIANT)")
run("""create or replace table RAW.PRODUCTS (product_id number(38,0), label varchar, category varchar, unit_price number(12,2), weight_kg number(38,10)) as
       select seq4()+1, 'Produit '||(seq4()+1), decode(uniform(0,4,random(5)),0,'frais',1,'epicerie',2,'boissons',3,'hygiene','bazar'),
              round(0.5 + uniform(0,2500,random(6))/100, 2), uniform(0,400,random(7))/10 from table(generator(rowcount => 1500))""", "RAW.PRODUCTS")
run("""create or replace table RAW.ORDERS (order_id number(38,0), customer_id number(38,0), order_ts timestamp_tz, total number(12,2), payment_method varchar, status varchar)
       cluster by (to_date(order_ts)) as
       select seq4()+1, uniform(1,20000,random(8)), dateadd(minute, uniform(0,864000,random(9)), '2024-01-01'::timestamp_tz),
              round(5 + uniform(0,50000,random(10))/100, 2), decode(uniform(0,2,random(11)),0,'card',1,'transfer','paypal'),
              decode(uniform(0,3,random(12)),0,'open',1,'paid',2,'shipped','returned') from table(generator(rowcount => 150000))""", "RAW.ORDERS (TIMESTAMP_TZ, clustering)")
run("""create or replace table RAW.ORDER_LINES (line_id number, order_id number, product_id number, qty number) as
       select seq4()+1, uniform(1,150000,random(13)), uniform(1,1500,random(14)), uniform(1,4,random(15)) from table(generator(rowcount => 450000))""", "RAW.ORDER_LINES")
run("""create or replace table RAW.EVENTS (event_id number, ts timestamp_ltz, actor object, props array, payload variant, geo geography) as
       select seq4()+1, dateadd(second, seq4(), '2024-06-01'::timestamp_ltz), object_construct('user_id','u'||uniform(1,5000,random(16)),'device','web'),
              array_construct(object_construct('k','page','v','/p/'||uniform(1,40,random(17)))), parse_json('{"cart":3,"flags":["a","b"],"ab":{"variant":"A"}}'),
              to_geography('POINT('||(2+uniform(0,100,random(18))/100)||' '||(48.5+uniform(0,100,random(19))/100)||')') from table(generator(rowcount => 200000))""", "RAW.EVENTS (OBJECT, ARRAY, VARIANT, GEOGRAPHY)")
run("create or replace transient table RAW.TMP_IMPORT (id number, note varchar)", "table transitoire")
run("create or replace table RAW.ORDERS_CLONE clone RAW.ORDERS", "clone")

# --- préparation : vues, vue sécurisée
run("create or replace view STAGING.STG_CUSTOMERS as select customer_id, lower(email) email, country, coalesce(segment,'inconnu') segment, signed_up_at from RAW.CUSTOMERS", "vue")
run("create or replace view STAGING.STG_ORDERS as select order_id, customer_id, order_ts, to_date(order_ts) order_date, total, status from RAW.ORDERS", "vue")
run("create or replace secure view STAGING.SV_CUSTOMERS_PII as select customer_id, email, full_name from RAW.CUSTOMERS", "vue sécurisée")

# --- modèle : tables, vue matérialisée (Enterprise), table dynamique
run("create or replace table MART.FCT_ORDERS cluster by (order_date) as select o.order_id, o.customer_id, o.order_date, o.total, o.status, c.country, c.segment from STAGING.STG_ORDERS o join STAGING.STG_CUSTOMERS c using (customer_id)", "MART.FCT_ORDERS (clustering)")
run("create or replace materialized view MART.MV_DAILY_REVENUE as select order_date, status, count(*) orders, sum(total) revenue from MART.FCT_ORDERS group by order_date, status", "vue matérialisée")
run("create or replace dynamic table MART.DT_COUNTRY_REVENUE target_lag = '1 day' warehouse = CANOT_WH as select country, sum(total) revenue, count(*) orders from MART.FCT_ORDERS group by country", "table dynamique")

# --- routines : SQL, JavaScript, Python
run("create or replace function MART.FX_TO_CENTS(x number(12,2)) returns number as 'cast(round(x*100) as number)'", "UDF SQL")
run("create or replace function MART.CLEAN_NAME(s varchar) returns varchar language javascript as 'return S == null ? null : S.toLowerCase().replace(/[^a-z0-9]+/g, \"_\");'", "UDF JavaScript")
run("""create or replace function MART.SLUG(s varchar) returns varchar language python runtime_version = '3.11' handler = 'f' as
       $$
def f(s):
    return None if s is None else ''.join(c if c.isalnum() else '-' for c in s.lower())
       $$""", "UDF Python")
run("""create or replace procedure MART.SP_REFRESH_MART() returns varchar language sql as
       $$ begin create or replace table MART.DIM_CUSTOMERS as select * from STAGING.STG_CUSTOMERS; return 'ok'; end; $$""", "procédure SQL")
run("""create or replace procedure MART.SP_PURGE(days float) returns varchar language javascript as
       $$ var r = snowflake.execute({sqlText: "select count(*) from RAW.EVENTS where ts < dateadd(day, -" + DAYS + ", current_timestamp())"}); return "ok"; $$""", "procédure JavaScript")

# --- flux, tâches, séquence, formats, stages, intégrations
run("create or replace stream OPS.ORDERS_STREAM on table RAW.ORDERS", "flux (stream)")
run("create or replace sequence OPS.SEQ_BATCH start = 1000 increment = 1", "séquence")
run("create or replace file format OPS.FF_CSV type = csv field_delimiter = ';' skip_header = 1", "format de fichier")
run("create or replace stage OPS.STG_INTERNE file_format = OPS.FF_CSV", "stage interne")
run("create or replace storage integration LAB_S3_INT type = external_stage storage_provider = 'S3' enabled = true storage_aws_role_arn = 'arn:aws:iam::123456789012:role/canot-lab' storage_allowed_locations = ('s3://canot-lab-inexistant/')", "intégration de stockage (factice)")
run("create or replace stage OPS.STG_EXTERNE url = 's3://canot-lab-inexistant/in/' storage_integration = LAB_S3_INT file_format = OPS.FF_CSV", "stage externe S3 (factice)")
run("create or replace task OPS.T_REFRESH warehouse = CANOT_WH schedule = 'USING CRON 0 3 * * * Europe/Paris' as call MART.SP_REFRESH_MART()", "tâche planifiée (suspendue)")
run("create or replace task OPS.T_ON_STREAM warehouse = CANOT_WH after OPS.T_REFRESH as insert into OPS.EVENTS_LOG select current_timestamp(), 'refresh'", "tâche dépendante")
run("create or replace table OPS.EVENTS_LOG (ts timestamp_ltz, msg varchar)", "journal d'exécution")

# --- gouvernance : politiques, étiquettes, rôles
run("create or replace masking policy OPS.MASK_EMAIL as (v varchar) returns varchar -> case when current_role() in ('CANOT_LAB') then v else '***' end", "politique de masquage")
run("alter table RAW.CUSTOMERS modify column email set masking policy OPS.MASK_EMAIL", "masquage appliqué")
run("create or replace row access policy OPS.RAP_COUNTRY as (c varchar) returns boolean -> case when current_role() in ('CANOT_LAB', 'ACCOUNTADMIN') then true else c = 'FR' end", "politique d'accès aux lignes (filtre partiel pour les autres rôles)")
run("alter table RAW.CUSTOMERS add row access policy OPS.RAP_COUNTRY on (country)", "politique d'accès appliquée")  # pas sur MART.FCT_ORDERS : la vue matérialisée l'interdit
run("create or replace tag OPS.PII allowed_values 'oui', 'non'", "étiquette")
run("alter table RAW.CUSTOMERS set tag OPS.PII = 'oui'", "étiquette posée")
for r in ("LAB_ANALYST", "LAB_ENGINEER", "LAB_FINANCE"):
    run(f"create role if not exists {r}", f"rôle {r}")
run("grant usage on database CANOT_LAB to role LAB_ANALYST")
run("grant usage on schema CANOT_LAB.MART to role LAB_ANALYST")
run("grant select on all tables in schema CANOT_LAB.MART to role LAB_ANALYST", "droits sur le modèle")
run("grant usage on database CANOT_LAB to role LAB_FINANCE")
run("grant select on all tables in schema CANOT_LAB.MART to role LAB_FINANCE")

# --- partage sortant (data sharing)
run("create or replace share LAB_SHARE", "partage sortant")
run("grant usage on database CANOT_LAB to share LAB_SHARE")
run("grant usage on schema CANOT_LAB.SHARED to share LAB_SHARE")

# --- le lecteur Canot lit la base du banc de test
for sql in ("grant usage on database CANOT_LAB to role CANOT_READER",
            "grant usage on all schemas in database CANOT_LAB to role CANOT_READER",
            "grant select on all tables in database CANOT_LAB to role CANOT_READER",
            "grant select on all views in database CANOT_LAB to role CANOT_READER"):
    run(sql, "droit du lecteur Canot : " + sql.split(" to ")[0][6:60])

# --- charge de travail réaliste : applications, étiquettes de requête, utilisateurs
work = [
    ("looker", "select country, sum(total) ca from MART.FCT_ORDERS where order_date >= '2024-06-01' group by country order by ca desc"),
    ("looker", "select order_date, revenue from MART.MV_DAILY_REVENUE where status = 'paid' order by order_date"),
    ("looker", "select segment, count(distinct customer_id) n from MART.FCT_ORDERS group by segment"),
    ("airflow", "select MART.CLEAN_NAME(full_name) n, count(*) from RAW.CUSTOMERS group by n limit 20"),
    ("airflow", "select count(*) from STAGING.STG_ORDERS where status = 'open'"),
    ("notebook", "select payload:ab.variant::string v, count(*) n from RAW.EVENTS group by v"),
    ("notebook", "select f.value:k::string k, count(*) n from RAW.EVENTS, lateral flatten(input => props) f group by k"),
    ("notebook", "select st_x(st_centroid(st_collect(geo))) lon from RAW.EVENTS"),
    ("sql-console", "select * from RAW.ORDERS at(offset => -30) limit 10"),
]
for _ in range(2):
    for app, q in work:
        run(q, f"charge {app}", tag=f'{{"app":"{app}"}}')
print(f"\n{done} créations/exécutions réussies, {len(skipped)} sautées")
for d, e in skipped:
    print("  -", d, "|", e)
con.close()
