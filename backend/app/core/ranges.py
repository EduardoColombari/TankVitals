"""Classificação de um valor pela faixa do cadastro (docs/ARQUITETURA.md §15).

É a mesma regra do front (`classify`, em `frontend/src/alerts.ts`):

* abaixo de `crit_min` ou acima de `crit_max` → crítico;
* fora de `ok_min`–`ok_max` → atenção;
* o resto → normal.

Os limites contam como dentro (28 °C ainda é normal). Limite `None` quer dizer
"sem limite daquele lado". Sem faixa, tudo é normal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Level = Literal["ok", "atencao", "critico"]

LEVEL_WEIGHT: dict[str, int] = {"ok": 0, "atencao": 1, "critico": 2}


@dataclass(frozen=True)
class Range:
    ok_min: float | None = None
    ok_max: float | None = None
    crit_min: float | None = None
    crit_max: float | None = None


def classify(value: float, rng: Range | None) -> Level:
    if rng is None:
        return "ok"
    if (rng.crit_min is not None and value < rng.crit_min) or (rng.crit_max is not None and value > rng.crit_max):
        return "critico"
    if (rng.ok_min is not None and value < rng.ok_min) or (rng.ok_max is not None and value > rng.ok_max):
        return "atencao"
    return "ok"
