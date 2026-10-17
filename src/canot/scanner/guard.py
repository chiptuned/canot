"""Garde lecture seule : tout SQL envoyé par le scan passe ici avant de partir. Un refus lève une exception ; rien n'est envoyé."""
from __future__ import annotations

import logging
import re

import sqlglot
from sqlglot import exp


class NotReadOnly(Exception):
    pass


_ALLOWED_ROOTS = (exp.Select, exp.Show, exp.Describe, exp.Union, exp.With, exp.Subquery)
_ALTER_SESSION = re.compile(r"^\s*alter\s+session\s+set\s+[A-Za-z_]+\s*=\s*('[^']*'|\"[^\"]*\"|[A-Za-z0-9_.]+)\s*;?\s*$", re.I)


_SYSTEM_FN = re.compile(r"\bsystem\$", re.I)  # SYSTEM$CANCEL_QUERY, SYSTEM$ABORT_SESSION... : des SELECT qui agissent


def check_readonly(sql: str, dialect: str) -> str:
    """Renvoie le SQL tel quel s'il est en lecture seule, sinon lève NotReadOnly."""
    if _SYSTEM_FN.search(sql):
        raise NotReadOnly("fonction SYSTEM$ refusée")
    if _ALTER_SESSION.match(sql):
        return sql
    log = logging.getLogger("sqlglot")
    level, log.level = log.level, logging.ERROR  # le repli en « Command » des SHOW est attendu, pas un avertissement
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception as e:  # noqa: BLE001
        raise NotReadOnly(f"SQL non analysable, refusé : {type(e).__name__}") from e
    finally:
        log.level = level
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise NotReadOnly("une seule instruction est admise")
    tree = trees[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        # SHOW/DESCRIBE sont parfois analysés comme des commandes brutes : on n'admet que ces verbes exacts
        if isinstance(tree, exp.Command) and str(tree.this).upper() in ("SHOW", "DESCRIBE", "DESC"):
            return sql
        raise NotReadOnly(f"instruction refusée : {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Into)) and node is not tree:
            raise NotReadOnly(f"écriture dans une sous-requête : {type(node).__name__}")
    return sql
