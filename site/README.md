# Site canot.io

Site statique (HTML, CSS, un seul script). Sans pisteur, sans police externe, sans style ni script en ligne (voir `_headers`).
Aperçu local : `python3 -m http.server -d site 8000`.

Déploiement (Cloudflare Pages) : `wrangler pages deploy site --project-name canot-site`, puis rattacher le domaine canot.io au projet.
Formulaire : définir le secret `CONTACT_WEBHOOK_URL` du projet (ex. un sujet ntfy). Sans lui, l'envoi échoue proprement et la page affiche l'adresse e-mail.

Logo : `assets/` (kit Canot, version retenue). Police du mot « canot » : Fredoka, licence SIL OFL (`assets/fonts/OFL.txt`).
