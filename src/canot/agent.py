"""Ce qu'un agent peut demander à Canot : la documentation et l'explication de vos rapports. Aucune connexion à vos plateformes :
les outils lisent des fichiers que vous désignez. `canot mcp` les expose en MCP (stdio)."""
from __future__ import annotations

import json
import re
from pathlib import Path

TOPICS = {"commandes": "commandes.md", "scan": "scan.md", "sur-site": "sur-site.md", "cibles": "cibles-ue.md", "ecarts": "ecarts.md"}
MAX_JSON_BYTES = 50_000_000
SCAN_SCHEMAS = ("canot.scan/1", "canot.scan/2")  # /2 = /1 + famille, capacités, liens, carte de sortie


def _docs_dir() -> Path:
    here = Path(__file__).parent
    for p in (here / "docs", here.parents[1] / "docs"):  # paquet installé, puis dépôt de développement
        if (p / "commandes.md").exists():
            return p
    raise FileNotFoundError("documentation introuvable")


def lire_doc(sujet: str) -> str:
    """Une page de documentation entière : commandes, scan, sur-site, cibles, ecarts."""
    key = sujet.strip().lower().removesuffix(".md")
    key = {"cibles-ue": "cibles", "ecart": "ecarts", "drill": "commandes", "mcp": "commandes"}.get(key, key)
    if key not in TOPICS:
        return f"Sujet inconnu : {sujet!r}. Sujets : {', '.join(TOPICS)}."
    return (_docs_dir() / TOPICS[key]).read_text(encoding="utf-8")


def _sections() -> list[tuple[str, str, str]]:
    out = []
    for topic, fname in TOPICS.items():
        title, buf = topic, []
        for line in (_docs_dir() / fname).read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                if buf:
                    out.append((topic, title, "\n".join(buf).strip()))
                title, buf = line.lstrip("# ").strip(), []
            else:
                buf.append(line)
        if buf:
            out.append((topic, title, "\n".join(buf).strip()))
    return [s for s in out if s[2]]


def chercher_doc(requete: str, limite: int = 5) -> list[dict]:
    """Passages de la documentation qui répondent à la requête (mots-clés), les plus pertinents d'abord."""
    words = [w for w in re.findall(r"\w+", requete.lower()) if len(w) > 2]
    scored = []
    for topic, title, text in _sections():
        hay_t, hay = title.lower(), text.lower()
        score = sum(3 * hay_t.count(w) + hay.count(w) for w in words)
        if score:
            scored.append((score, {"page": topic, "section": title, "texte": text[:1500]}))
    return [s for _, s in sorted(scored, key=lambda x: -x[0])[:limite]]


def commandes() -> str:
    """Le tableau des commandes et leurs options."""
    text = lire_doc("commandes")
    start = text.index("## Les commandes")
    end = text.index("## Les sources")
    return text[start:end].strip()


def _load(path: str) -> dict:
    p = Path(path).expanduser()
    if p.suffix != ".json" or not p.is_file():
        raise ValueError("indiquez le chemin d'un fichier .json produit par Canot (scan.json ou report.json)")
    if p.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("fichier trop gros")
    return json.loads(p.read_text(encoding="utf-8"))


def _fig(f) -> str:
    if not isinstance(f, dict):
        return str(f)
    n = f.get("nature")
    if n in ("non_lu", "non_estimé"):
        return f"{n} ({f.get('raison')})"
    if n == "estimé":
        return f"estimé : {f.get('bas')} à {f.get('haut')} {f.get('unite') or ''}".strip()
    return f"{n} : {f.get('valeur')} {f.get('unite') or ''}".strip()


def expliquer_scan(chemin: str, max_constats: int = 40) -> dict:
    """Résumé d'un scan.json : périmètre, constats « à examiner », lectures non faites, ce qui n'est pas chiffré."""
    r = _load(chemin)
    if r.get("schema") not in SCAN_SCHEMAS:
        raise ValueError("ce fichier n'est pas un scan.json de Canot")
    flags = [{"code": f["code"], "cible": f["cible"], "observe": f["texte"]} for f in r.get("drapeaux", [])[:max_constats]]
    nl = [{"lecture": f"{l['id']} {l['vue']}", "raison": l.get("raison")} for l in r.get("lectures", []) if l.get("statut") == "non_lu"]
    return {
        "plateforme": r.get("plateforme"), "date": r.get("as_of"), "perimetre": r.get("perimetre"), "niveau_droits": r.get("niveau_droits"),
        "resume": r.get("resume"), "constats": flags, "constats_total": len(r.get("drapeaux", [])),
        "lectures_non_faites": nl, "non_estime": r.get("non_estime"), "fraicheur": r.get("fraicheur"),
        "rejouabilite": (r.get("rejouabilite") or {}).get("base"),
        # /2 seulement ; None sur un scan.json /1
        "famille": r.get("famille"), "capacites": r.get("capacites"), "carte_sortie": r.get("carte_sortie") or None,
        "non_prouvable": _non_prouvable(r),
        "a_savoir": ["Chaque constat est « À examiner », sans niveau de gravité : il dit ce qui est observé et ce qui ne l'est pas.",
                     "Une absence de constat ne vaut que pour les lectures faites.",
                     "Accepté par DuckDB ne veut pas dire résultat identique : seul canot drill compare les résultats.",
                     "Un scan inventorie : il n'exporte rien et ne prouve rien. Export et preuve sont des capacités de canot drill "
                     "(canot rejeu-dbt pour la preuve P3 d'un projet dbt)."],
    }


def _non_prouvable(r: dict) -> dict | None:
    """Ce qui est compté sans pouvoir être prouvé hors plateforme : objets « à reconstruire » et constats F13 à F16."""
    if r.get("schema") != "canot.scan/2":
        return None
    a_rec = sum((r.get("carte_sortie") or {}).get("a_reconstruire", {}).values())
    codes = {}
    for f in r.get("drapeaux", []):
        if f["code"] in ("F13", "F14", "F15", "F16"):
            codes[f["code"]] = codes.get(f["code"], 0) + 1
    preuve = [p.get("forme") for p in (r.get("capacites") or {}).get("preuve") or []]
    return {"objets_a_reconstruire": a_rec, "constats": codes, "formes_de_preuve_declarees": preuve}


def capacites(plateforme: str | None = None) -> dict:
    """Ce que Canot sait faire par plateforme (inventaire, export, preuve P1-P5), son statut de validation et la date de test consignée.
    Lu dans le registre des connecteurs, jamais deviné."""
    from . import connectors

    if plateforme:
        c = connectors.get(plateforme.strip().lower())
        if c is None:
            return {"erreur": f"Plateforme inconnue : {plateforme!r}. Plateformes : {', '.join(connectors.CONNECTORS)}."}
        items = [c.capacites()]
    else:
        items = [c.capacites() for c in connectors.CONNECTORS.values()]
    return {"connecteurs": items, "formes_de_preuve": connectors.PREUVES,
            "a_savoir": ["statut « testé » : vu sur une vraie plateforme ; « écrit, non testé » : code présent, jamais exécuté sur un vrai compte.",
                         "teste_sur : date et objet du test consignés dans la documentation du dépôt ; vide si aucun test n'y est consigné.",
                         "Inventaire = canot scan ; export et preuve = canot drill (P3 d'un projet dbt : canot rejeu-dbt). "
                         "Une forme de preuve absente n'est remplacée par aucun score."]}


def expliquer_rapport(chemin: str, max_lignes: int = 30) -> dict:
    """Résumé d'un report.json d'exercice (parité, tables et requêtes en écart, pistes du catalogue des écarts)
    ou d'un rejeu-dbt.json (preuve P3 : modèles identiques, différents, non comparés, non rejouables et leurs raisons)."""
    r = _load(chemin)
    if r.get("schema") == "canot.rejeu-dbt/1":
        mods = r.get("modeles") or []
        return {"resume": r.get("summary"), "forme": "P3 rejeu de traitements",
                "differents": [{"modele": m["modele"], "raison": m.get("raison")} for m in mods if m.get("statut") == "different"][:max_lignes],
                "non_rejouables": [{"modele": m["modele"], "raison": m.get("raison")} for m in mods if m.get("statut") == "non_rejouable"][:max_lignes],
                "non_compares": [m["modele"] for m in mods if m.get("statut") == "non_compare"][:max_lignes],
                "regles": r.get("regles"),
                "a_savoir": ["« identique » : même SQL compilé, entrées copiées, sortie DuckDB comparée à la table d'origine.",
                             "Un modèle non rejouable ou non comparé n'est pas prouvé : il est compté avec sa raison.",
                             "La signature du dossier atteste son intégrité, pas l'auteur ni la date."]}
    if "summary" not in r or "checks" not in r:
        raise ValueError("ce fichier n'est pas un report.json de Canot")
    bad_t = [{"table": c["table"], "lignes_source": c.get("rows_source"), "lignes_copie": c.get("rows_target"), "schema_ok": c.get("schema_ok"),
              "statistiques_ok": c.get("stats_ok"), "empreinte_ok": c.get("fingerprint_ok"), "ecarts_statistiques": c.get("stats_diffs", [])[:5],
              "precision_seulement": bool(c.get("precision_cols")) and c.get("stats_ok") is not False}
             for c in r["checks"] if not c.get("ok")][:max_lignes]
    bad_q = [{"statut": q.get("status"), "detail": str(q.get("detail"))[:300], "requete": str(q.get("sql"))[:200]}
             for q in r.get("queries", []) if q.get("status") != "match"][:max_lignes]
    try:
        ecarts = [l.lstrip("# ").strip() for l in lire_doc("ecarts").splitlines() if l.startswith("## ")]
    except OSError:
        ecarts = []
    return {"resume": r["summary"], "tables_en_ecart": bad_t, "requetes_en_ecart": bad_q, "catalogue_des_ecarts": ecarts,
            "a_savoir": ["Les requêtes citées viennent de votre historique : ne les transmettez pas sans les relire.",
                         "Un écart de précision seule (nanosecondes avec Iceberg v2) est à traiter, ce n'est pas une corruption.",
                         "La signature du dossier atteste son intégrité, pas l'auteur ni la date."]}


TOOLS = (commandes, chercher_doc, lire_doc, capacites, expliquer_scan, expliquer_rapport)


def serve() -> None:
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:
            raise ModuleNotFoundError("Il manque le connecteur MCP. Lancez : uvx --from \"canot[mcp]\" canot mcp", name="mcp") from e
    server = Server("canot")
    for fn in TOOLS:
        server.tool()(fn)
    server.run()
