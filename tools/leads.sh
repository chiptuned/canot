#!/usr/bin/env bash
# Lit les demandes reçues par le formulaire de canot.io (base D1 « canot-leads », juridiction UE).
# Usage : tools/leads.sh [nombre]   (par défaut les 10 dernières)
set -euo pipefail
cd "$(dirname "$0")/../site"
wrangler d1 execute canot-leads --remote --json --command "SELECT id, created_at, name, company, email, platform, renewal, message FROM leads ORDER BY id DESC LIMIT ${1:-10}" \
  | python3 -c "import sys,json; [print(f\"#{r['id']}  {r['created_at'][:16]}  {r['name']} ({r['company']})  {r['email']}  {r['platform']}  renouvellement {r['renewal']}\" + (f\"\n     {r['message']}\" if r['message'] else '')) for r in json.load(sys.stdin)[0]['results']]"
