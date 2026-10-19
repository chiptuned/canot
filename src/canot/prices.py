"""Public list prices, dated and sourced. They are reference points, not your contract rates:
replace them with your prices through --credit-price (Snowflake) when you know them."""
from __future__ import annotations

AS_OF = "17/10/2026"

# Snowflake: credit price in USD, AWS Paris / Frankfurt, by edition (about 30% more than US East)
SNOWFLAKE_CREDIT_USD = {"standard": 2.60, "enterprise": 3.90, "business_critical": 5.20}
SNOWFLAKE_SOURCE = "https://www.snowflake.com/pricing/pricing-guide/"

# BigQuery: on-demand queries, USD per TiB beyond the first free TiB of the month (US rate; EU region to confirm)
BIGQUERY_ON_DEMAND_USD_PER_TIB = 6.25
BIGQUERY_SOURCE = "https://cloud.google.com/bigquery/pricing"

# Rework for the objects that do not move on their own, in days: q4o assumptions, to discuss with the client
REWORK_DAYS_PER_OBJECT = (0.25, 1.5)
REWORK_DAYS_PER_NONPORTABLE_COLUMN = (0.1, 0.5)


def snowflake_range(credits: float) -> tuple[float, float]:
    lo, hi = min(SNOWFLAKE_CREDIT_USD.values()), max(SNOWFLAKE_CREDIT_USD.values())
    return round(credits * lo, 2), round(credits * hi, 2)


def snowflake_cost(credits: float, credit_price: float | None) -> tuple[list[float], str]:
    """(range or value in USD, basis of the calculation)."""
    if credit_price:
        v = round(credits * credit_price, 2)
        return [v, v], f"credit price provided: {credit_price} USD"
    lo, hi = snowflake_range(credits)
    return [lo, hi], (f"Snowflake list price as of {AS_OF}, AWS Paris/Frankfurt, from lowest "
                      f"({min(SNOWFLAKE_CREDIT_USD.values())} USD, Standard) to highest ({max(SNOWFLAKE_CREDIT_USD.values())} USD, Business Critical): {SNOWFLAKE_SOURCE}")


def rework_days(objects: int, nonportable_columns: int) -> list[float]:
    lo = objects * REWORK_DAYS_PER_OBJECT[0] + nonportable_columns * REWORK_DAYS_PER_NONPORTABLE_COLUMN[0]
    hi = objects * REWORK_DAYS_PER_OBJECT[1] + nonportable_columns * REWORK_DAYS_PER_NONPORTABLE_COLUMN[1]
    return [round(lo, 1), round(hi, 1)]
