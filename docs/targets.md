# Targets: sovereign hosting providers and countries

Canot writes to any S3-compatible storage. Built-in shortcuts (`--to <provider>:<region> --bucket <name>`).
**Check each endpoint in the provider's console before use: they are still to be confirmed.**

| Shortcut | Provider | Country | Note |
|---|---|---|---|
| `scaleway:fr-par` (also `nl-ams`, `pl-waw`) | Scaleway | FR, NL, PL | not SecNumCloud-qualified (to be checked) |
| `ovh:gra` | OVHcloud | FR | SNC offering qualified SecNumCloud (to be checked on the ANSSI list) |
| `outscale:eu-west-2` | Outscale | FR | qualification up for renewal at the end of 2026 (to be checked) |
| `ionos:eu-central-1` | IONOS | DE | |
| `stackit:eu01` | STACKIT (Schwarz Group) | DE | |
| `aws:eu-west-3` | AWS | FR | not a sovereign cloud; AWS European Sovereign Cloud also exists |

## The same principle in other EU countries

The Data Act applies across the EU: same provider switching rules (Art. 23 to 31), same charges down to zero from 12 Jan 2027.
What changes from one country to another: the financial supervisor that enforces DORA, the authority that oversees the Data Act, and national sovereignty requirements.

| Country | Why it is relevant | Local hosting providers |
|---|---|---|
| Germany | largest market; BaFin enforces DORA; strong sovereignty culture | STACKIT, IONOS, Open Telekom Cloud |
| Netherlands | financial center, many scale-ups on Snowflake | Leaseweb |
| Italy | many banks and insurers | Aruba, Seeweb |
| Spain | banks; Data Act through the CNMC | IONOS, Arsys |
| Poland, Nordic countries | heavy cloud use, local hosting providers | Scaleway Warsaw, UpCloud |

To confirm before selling: the competent authority designated in each country and the national qualification lists (SecNumCloud equivalents: C5 in Germany, ACN in Italy, ENS in Spain).
