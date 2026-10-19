"""Read-only guard: every SQL statement sent by the scan goes through here before it leaves. A refusal raises an exception; nothing is sent."""
from __future__ import annotations

import logging
import re

import sqlglot
from sqlglot import exp


class NotReadOnly(Exception):
    pass


_ALLOWED_ROOTS = (exp.Select, exp.Show, exp.Describe, exp.Union, exp.With, exp.Subquery)
_ALTER_SESSION = re.compile(r"^\s*alter\s+session\s+set\s+[A-Za-z_]+\s*=\s*('[^']*'|\"[^\"]*\"|[A-Za-z0-9_.]+)\s*;?\s*$", re.I)


_SYSTEM_FN = re.compile(r"\bsystem\$", re.I)  # SYSTEM$CANCEL_QUERY, SYSTEM$ABORT_SESSION...: SELECTs that act


def check_readonly(sql: str, dialect: str) -> str:
    """Returns the SQL as is if it is read-only, otherwise raises NotReadOnly."""
    if _SYSTEM_FN.search(sql):
        raise NotReadOnly("SYSTEM$ function refused")
    if _ALTER_SESSION.match(sql):
        return sql
    log = logging.getLogger("sqlglot")
    level, log.level = log.level, logging.ERROR  # SHOW falling back to "Command" is expected, not a warning
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception as e:  # noqa: BLE001
        raise NotReadOnly(f"SQL not parseable, refused: {type(e).__name__}") from e
    finally:
        log.level = level
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise NotReadOnly("only one statement is allowed")
    tree = trees[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        # SHOW/DESCRIBE are sometimes parsed as raw commands: only these exact verbs are allowed
        if isinstance(tree, exp.Command) and str(tree.this).upper() in ("SHOW", "DESCRIBE", "DESC"):
            return sql
        raise NotReadOnly(f"statement refused: {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Into)) and node is not tree:
            raise NotReadOnly(f"write in a subquery: {type(node).__name__}")
    return sql
