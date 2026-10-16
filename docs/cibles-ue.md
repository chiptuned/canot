# Cibles : hébergeurs souverains et pays

Canot écrit sur tout stockage compatible S3. Raccourcis intégrés (`--to <hébergeur>:<région> --bucket <nom>`).
**Vérifiez chaque point de terminaison dans la console de l'hébergeur avant usage : ils sont à confirmer.**

| Raccourci | Hébergeur | Pays | Remarque |
|---|---|---|---|
| `scaleway:fr-par` (aussi `nl-ams`, `pl-waw`) | Scaleway | FR, NL, PL | pas qualifié SecNumCloud (à vérifier) |
| `ovh:gra` | OVHcloud | FR | offre SNC qualifiée SecNumCloud (à vérifier sur la liste ANSSI) |
| `outscale:eu-west-2` | Outscale | FR | qualification à renouveler fin 2026 (à vérifier) |
| `ionos:eu-central-1` | IONOS | DE | |
| `stackit:eu01` | STACKIT (groupe Schwarz) | DE | |
| `aws:eu-west-3` | AWS | FR | pas un cloud souverain ; AWS European Sovereign Cloud existe aussi |

## Le même principe dans d'autres pays de l'UE

Le Data Act s'applique dans toute l'UE : mêmes règles de changement de fournisseur (art. 23 à 31), mêmes frais à zéro à partir du 12 janvier 2027.
Ce qui change d'un pays : le superviseur financier qui applique DORA, l'autorité qui contrôle le Data Act, et les exigences nationales de souveraineté.

| Pays | Pourquoi c'est intéressant | Hébergeurs locaux |
|---|---|---|
| Allemagne | plus gros marché ; BaFin applique DORA ; forte culture souveraineté | STACKIT, IONOS, Open Telekom Cloud |
| Pays-Bas | place financière, beaucoup de scale-ups sur Snowflake | Leaseweb |
| Italie | banques et assurances nombreuses | Aruba, Seeweb |
| Espagne | banques ; Data Act via la CNMC | IONOS, Arsys |
| Pologne, pays nordiques | fort usage du cloud, hébergeurs locaux | Scaleway Varsovie, UpCloud |

À confirmer avant de vendre : l'autorité compétente désignée dans chaque pays et les listes de qualification nationales (équivalents de SecNumCloud : C5 en Allemagne, ACN en Italie, ENS en Espagne).
