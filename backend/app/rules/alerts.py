"""Alertas de faixa: o sensor saiu do normal (IOT-07).

Máquina de estados em memória, sem banco: recebe as leituras e diz o que mudou
(`open`, `escalate`, `close`). Quem chamou grava no PostgreSQL e publica.

* abre na primeira leitura fora da faixa normal;
* **piora** (atenção → crítico) atualiza o mesmo alerta;
* fecha só depois de `close_after` leituras seguidas dentro da faixa. O número
  evita o abre-fecha-abre quando o valor balança em cima do limite. É o mesmo
  critério do modo simulado do front (30 leituras).

Os alertas **da IA** (`field` nulo) são da IA-08, no `inference`: este
rastreador só cuida de variáveis com faixa.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ranges import LEVEL_WEIGHT, Level, Range, classify


@dataclass(frozen=True)
class FieldSpec:
    device: str
    field: str
    label: str
    rng: Range


@dataclass
class OpenAlert:
    device: str
    field: str
    level: str
    value: float
    message: str
    opened_at: str
    db_id: object | None = None  # a chave da linha no PostgreSQL, preenchida quando o alerta é gravado
    ok_streak: int = 0

    @property
    def public_id(self) -> str:
        """O id que a tela vê (`a-118`, como no exemplo da §12.1)."""
        return f"a-{self.db_id}"


@dataclass(frozen=True)
class AlertChange:
    kind: str  # "open" | "escalate" | "close"
    alert: OpenAlert
    ts: str


def alert_message(label: str, level: str) -> str:
    """O mesmo texto do modo simulado do front: `Oxigênio dissolvido fora da faixa normal`."""
    return f"{label} {'em nível crítico' if level == 'critico' else 'fora da faixa normal'}"


class RangeAlertTracker:
    def __init__(self, *, close_after: int = 30):
        self._close_after = close_after
        self._specs: dict[tuple[str, str], FieldSpec] = {}
        self._open: dict[tuple[str, str], OpenAlert] = {}

    @property
    def open_alerts(self) -> list[OpenAlert]:
        return list(self._open.values())

    def restore(self, alerts: list[OpenAlert]) -> None:
        """Retoma os alertas que estavam abertos no banco, para um reinício não duplicar."""
        for alert in alerts:
            self._open[(alert.device, alert.field)] = alert

    def set_fields(self, specs: list[FieldSpec], ts: str) -> list[AlertChange]:
        """Troca as faixas (recarga do banco). Fecha o alerta de quem saiu do cadastro ou ficou sem faixa."""
        self._specs = {(spec.device, spec.field): spec for spec in specs if spec.rng != Range()}
        changes: list[AlertChange] = []
        for key in [k for k in self._open if k not in self._specs]:
            alert = self._open.pop(key)
            changes.append(AlertChange("close", alert, ts))
        return changes

    def discard(self, device: str, field_key: str) -> None:
        """Esquece um alerta que não deu para gravar, para a próxima leitura tentar abrir de novo."""
        self._open.pop((device, field_key), None)

    def update(self, device: str, field_key: str, value: float, ts: str) -> AlertChange | None:
        spec = self._specs.get((device, field_key))
        if spec is None:
            return None
        level: Level = classify(value, spec.rng)
        key = (device, field_key)
        current = self._open.get(key)

        if level != "ok":
            if current is None:
                alert = OpenAlert(device, field_key, level, value, alert_message(spec.label, level), ts)
                self._open[key] = alert
                return AlertChange("open", alert, ts)
            current.ok_streak = 0
            if LEVEL_WEIGHT[level] > LEVEL_WEIGHT[current.level]:
                current.level = level
                current.value = value
                current.message = alert_message(spec.label, level)
                return AlertChange("escalate", current, ts)
            return None

        if current is not None:
            current.ok_streak += 1
            if current.ok_streak >= self._close_after:
                del self._open[key]
                return AlertChange("close", current, ts)
        return None
