from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TOML = """\
# Tolérances de comparaison, à relire avec le client AVANT l'exercice.
[tolerance]
float_rel = 1e-9     # écart relatif admis sur les nombres à virgule flottante
decimal_abs = 0.0    # écart absolu admis sur les montants décimaux (ex. 0.01 pour un centime)

[tolerance.columns]  # surcharge par colonne : "SCHEMA.TABLE.COLONNE" = écart absolu
# "PUBLIC.ORDERS.TOTAL" = 0.01
"""


@dataclass
class Tolerances:
    float_rel: float = 1e-9
    decimal_abs: float = 0.0
    columns: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | None) -> "Tolerances":
        if not path:
            return cls()
        d = tomllib.loads(Path(path).read_text()).get("tolerance", {})
        return cls(float(d.get("float_rel", 1e-9)), float(d.get("decimal_abs", 0.0)),
                   {k.upper(): float(v) for k, v in d.get("columns", {}).items()})

    def abs_for(self, table_fqn: str, column: str) -> float | None:
        return self.columns.get(f"{table_fqn}.{column}".upper())

    def to_dict(self) -> dict:
        return {"float_rel": self.float_rel, "decimal_abs": self.decimal_abs, "columns": self.columns}


def equal(a, b, tol: Tolerances, abs_override: float | None = None) -> bool:
    from datetime import datetime
    from decimal import Decimal

    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)) and not isinstance(a, bool):
        if isinstance(a, float) or isinstance(b, float):
            fa, fb = float(a), float(b)
            if fa == fb:
                return True
            if abs_override is not None and abs(fa - fb) <= abs_override:
                return True
            return abs(fa - fb) <= tol.float_rel * max(abs(fa), abs(fb))
        lim = abs_override if abs_override is not None else tol.decimal_abs
        return abs(Decimal(a) - Decimal(b)) <= Decimal(str(lim))
    if isinstance(a, datetime) and isinstance(b, datetime):
        if (a.tzinfo is None) != (b.tzinfo is None):
            a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
        return a == b
    return a == b or str(a) == str(b)
