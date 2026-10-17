# Sécurité

## Signaler une vulnérabilité

Écrivez à **vincent@q4o.com** avec « Canot sécurité » en objet. N'ouvrez pas d'issue publique pour une faille.
Accusé de réception sous 3 jours ouvrés, correctif ou plan sous 30 jours.

## Ce que fait Canot de vos données

- Il tourne dans vos comptes : la source est lue en lecture seule, la copie est écrite dans un stockage à vous.
- Il n'envoie rien à q4o ni à un tiers : pas de télémétrie, pas de serveur.
- Les identifiants passent par des variables d'environnement ou la configuration locale de la CLI du fournisseur, jamais par la ligne de commande.
- La clé qui signe le dossier est créée sur la machine qui lance l'exercice (`~/.canot/evidence.key`, droits 600).

## Ce que la signature prouve, et ce qu'elle ne prouve pas

Elle prouve que le journal n'a pas été modifié depuis sa signature (`canot verify-evidence`). Elle ne prouve ni l'identité de l'auteur, ni la date. Pour une preuve datée, archivez le dossier dans un système d'archivage horodaté.

## Versions prises en charge

La dernière version publiée sur PyPI. Le projet est en alpha.
