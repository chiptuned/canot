# Module OpenTofu : Scaleway

Crée deux buckets (données versionné, preuves verrouillé) et, si `with_vm=true`, une VM privée avec le catalogue Lakekeeper.
Rien n'est créé sans votre `tofu apply`, après relecture du plan.

```bash
export SCW_ACCESS_KEY=... SCW_SECRET_KEY=...
tofu init && tofu plan -var project_id=<ID>
tofu apply -var project_id=<ID>
```
Sans VM, les deux buckets suffisent pour un exercice lancé depuis le poste de l'admin. Coût : quelques centimes par mois pour un jeu de test.
