"""Prix catalogue publics, datés et sourcés. Ce sont des repères, pas le tarif de votre contrat :
remplacez-les par vos prix avec --credit-price (Snowflake) quand vous les connaissez."""
from __future__ import annotations

AS_OF = "17/10/2026"

# Snowflake : prix du crédit en USD, AWS Paris / Francfort, selon l'édition (environ 30 % de plus qu'en US East)
SNOWFLAKE_CREDIT_USD = {"standard": 2.60, "enterprise": 3.90, "business_critical": 5.20}
SNOWFLAKE_SOURCE = "https://www.snowflake.com/pricing/pricing-guide/"

# BigQuery : requêtes à la demande, USD par TiB au-delà du premier TiB gratuit du mois (tarif US ; région UE à confirmer)
BIGQUERY_ON_DEMAND_USD_PER_TIB = 6.25
BIGQUERY_SOURCE = "https://cloud.google.com/bigquery/pricing"

# Travail de reprise des objets qui ne partent pas seuls, en jours : hypothèses de q4o, à discuter avec le client
REWORK_DAYS_PER_OBJECT = (0.25, 1.5)
REWORK_DAYS_PER_NONPORTABLE_COLUMN = (0.1, 0.5)


def snowflake_range(credits: float) -> tuple[float, float]:
    lo, hi = min(SNOWFLAKE_CREDIT_USD.values()), max(SNOWFLAKE_CREDIT_USD.values())
    return round(credits * lo, 2), round(credits * hi, 2)


def snowflake_cost(credits: float, credit_price: float | None) -> tuple[list[float], str]:
    """(fourchette ou valeur en USD, base du calcul)."""
    if credit_price:
        v = round(credits * credit_price, 2)
        return [v, v], f"prix du crédit fourni : {credit_price} $"
    lo, hi = snowflake_range(credits)
    return [lo, hi], (f"prix catalogue Snowflake du {AS_OF}, AWS Paris/Francfort, du plus bas "
                      f"({min(SNOWFLAKE_CREDIT_USD.values())} $, Standard) au plus haut ({max(SNOWFLAKE_CREDIT_USD.values())} $, Business Critical) : {SNOWFLAKE_SOURCE}")


def rework_days(objects: int, nonportable_columns: int) -> list[float]:
    lo = objects * REWORK_DAYS_PER_OBJECT[0] + nonportable_columns * REWORK_DAYS_PER_NONPORTABLE_COLUMN[0]
    hi = objects * REWORK_DAYS_PER_OBJECT[1] + nonportable_columns * REWORK_DAYS_PER_NONPORTABLE_COLUMN[1]
    return [round(lo, 1), round(hi, 1)]
