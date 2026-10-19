#!/usr/bin/env bash
# Reads the requests received by the canot.io form (D1 database "canot-leads", EU jurisdiction).
# Usage: tools/leads.sh [count]   (the last 10 by default)
set -euo pipefail
cd "$(dirname "$0")/../site"
wrangler d1 execute canot-leads --remote --json --command "SELECT id, created_at, name, company, email, platform, renewal, message, source, partage FROM leads ORDER BY id DESC LIMIT ${1:-10}" \
  | python3 -c "import sys,json; [print(f\"#{r['id']}  {r['created_at'][:16]}  {r['name']} ({r['company']})  {r['email']}  {r['platform']}  renewal {r['renewal']}  [{r['source']}]\" + (f\"\n     {r['message']}\" if r['message'] else '') + (f\"\n     report: {r['partage']}\" if r['partage'] else '')) for r in json.load(sys.stdin)[0]['results']]"
