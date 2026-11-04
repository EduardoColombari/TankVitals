"""A regra do aerador, sem rede nem banco (IOT-07).

Regra com **dois limites**: liga com OD < `on_below` (5 mg/L) e desliga com
OD ≥ `off_at` (6,5 mg/L). Entre os dois, não faz nada e o atuador fica como
está. Essa distância (histerese) evita o liga-desliga a cada leitura: com um
limite só, o oxigênio balançando em torno de 5 ligaria e desligaria o aerador
o tempo todo.

**Um comando por mudança, não um por leitura.** O motor guarda o último
comando enviado a cada atuador e só emite quando o desejado é diferente.

Duas proteções além do enunciado, porque o simulador (ou o aerador real)
reinicia:

* **Estado de partida.** Antes de qualquer comando, o estado do atuador vem da
  própria telemetria dele (`{"on": ...}`). Assim, ao subir com o aerador já
  desligado e o oxigênio alto, não sai um "desligar" à toa.
* **Comando que não pegou.** Se o atuador informa um estado diferente do último
  comando por mais de `grace_s`, o motor esquece o comando e reavalia. Sem isso,
  um simulador que reinicia (aerador volta desligado) deixaria o aerador
  desligado para sempre com o oxigênio baixo, porque "o último comando foi
  ligar".
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleSpec:
    id: str
    name: str
    device: str  # sensor vigiado
    field: str  # variável vigiada
    actuator: str
    on_below: float
    off_at: float


@dataclass(frozen=True)
class Decision:
    rule: RuleSpec
    actuator: str
    on: bool
    value: float  # a leitura que causou a decisão


@dataclass
class _Sent:
    on: bool
    at: float


class RuleEngine:
    def __init__(self, *, grace_s: float = 20.0):
        self._grace_s = grace_s
        self._rules: list[RuleSpec] = []
        self._sent: dict[str, _Sent] = {}  # atuador → último comando enviado
        self._reported: dict[str, bool] = {}  # atuador → último estado informado por ele

    def set_rules(self, rules: list[RuleSpec]) -> None:
        """Troca as regras (recarga do banco). Descarta as inválidas, em vez de travar o serviço."""
        valid: list[RuleSpec] = []
        for rule in rules:
            if not (math.isfinite(rule.on_below) and math.isfinite(rule.off_at)) or rule.off_at <= rule.on_below:
                logger.error(
                    "Regra %s ignorada: precisa de off_at (%s) maior que on_below (%s)", rule.id, rule.off_at, rule.on_below
                )
                continue
            valid.append(rule)
        self._rules = valid

    @property
    def rules(self) -> list[RuleSpec]:
        return list(self._rules)

    @property
    def watched_actuators(self) -> set[str]:
        return {rule.actuator for rule in self._rules}

    # --- o que o atuador diz de si ----------------------------------------------

    def observe_state(self, actuator: str, on: bool, now: float) -> None:
        self._reported[actuator] = on
        sent = self._sent.get(actuator)
        if sent is not None and sent.on != on and now - sent.at > self._grace_s:
            logger.info("%s informa on=%s, mas o último comando foi on=%s: reavaliando", actuator, on, sent.on)
            del self._sent[actuator]

    def _current(self, actuator: str) -> bool | None:
        sent = self._sent.get(actuator)
        if sent is not None:
            return sent.on
        return self._reported.get(actuator)

    def rollback(self, actuator: str) -> None:
        """Esquece o último comando de um atuador que não deu para publicar: a próxima leitura tenta de novo."""
        self._sent.pop(actuator, None)

    # --- a leitura do sensor -----------------------------------------------------

    def evaluate(self, device: str, payload: dict[str, Any], now: float) -> list[Decision]:
        """Avalia as leituras de um sensor e devolve os comandos a enviar (quase sempre nenhum)."""
        decisions: list[Decision] = []
        for rule in self._rules:
            if rule.device != device:
                continue
            value = payload.get(rule.field)
            # `bool` é subclasse de `int` no Python: um `true` não é uma leitura.
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                continue
            current = self._current(rule.actuator)
            if value < rule.on_below:
                wanted = True
            elif value >= rule.off_at:
                wanted = False
            else:
                continue  # dentro da histerese: fica como está
            if wanted == current:
                continue
            if current is None and wanted is False:
                continue  # não sei o estado e o atuador não precisa ligar: não mando "desligar" às cegas
            self._sent[rule.actuator] = _Sent(wanted, now)
            decisions.append(Decision(rule, rule.actuator, wanted, float(value)))
        return decisions
