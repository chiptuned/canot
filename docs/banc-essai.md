# Banc d'essai

Mesures réelles, avec les conditions. Elles décrivent le comportement de Canot, pas celui d'une plateforme : le débit dépend du réseau, de la machine et de l'entrepôt de la source.

## TPC-H SF10 depuis Snowflake vers un disque local (17/10/2026)

| | |
|---|---|
| Source | Snowflake, compte d'essai, jeu d'exemple TPC-H SF10, entrepôt XSMALL |
| Destination | dossier local (Apache Iceberg, 2,8 Go de fichiers Parquet) |
| Volume | 8 tables, **86 586 082 lignes**, environ 10,6 Go lus (la plus grosse table : 59 986 052 lignes, 7,3 Go) |
| Résultat | 8 tables sur 8 au contenu identique (nombre de lignes, statistiques par colonne, empreinte du contenu) ; 2 requêtes sur 2 identiques |
| Durée totale | 31 min 36 s (copie, comparaison et rejeu compris) |
| Débit de copie | environ 7 Mo/s, limité par la lecture depuis l'entrepôt d'essai |
| Mémoire | pic de 2,4 Go sur la machine qui lance l'exercice (MacBook Air), pour 10,6 Go de données : la comparaison lit la copie par lots |
| Consommation | environ 0,41 crédit Snowflake |
| Version | Canot 0.1.0 + correctifs de vérification par lots, DuckDB 1.5.6, pyiceberg 0.12 |

Limites connues : le rejeu des requêtes charge en mémoire les tables qu'elles touchent (option `--replay-max-gb`, 2 Go par défaut) ; ici, seules des requêtes sur de petites tables ont été rejouées. Aucun essai au-delà de 10 Go.
