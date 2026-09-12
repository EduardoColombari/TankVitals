<script setup lang="ts">
import { computed } from 'vue'
import type { HistorySeries, MetricKey } from '../types'
import { METRIC_META } from '../types'

const props = defineProps<{
  history: HistorySeries | null
  metric: MetricKey
}>()

const rows = computed(() => {
  const series = props.history?.series.find((item) => item.metric === props.metric)
  if (!series) return []

  return [...series.points]
    .sort((a, b) => +new Date(b.t) - +new Date(a.t))
    .slice(0, 50)
    .map((point) => ({
      time: new Date(point.t),
      value: point.v,
    }))
})

function formatDate(date: Date): string {
  return date.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function levelFor(value: number): 'ok' | 'atencao' | 'critico' {
  if (props.metric === 'temperature_c') {
    if (value < 22 || value > 30) return 'critico'
    if (value < 24 || value > 28) return 'atencao'
  } else if (props.metric === 'ph') {
    if (value < 6 || value > 8.5) return 'critico'
    if (value < 6.5 || value > 8) return 'atencao'
  } else if (props.metric === 'level_pct') {
    if (value < 15) return 'critico'
    if (value < 30) return 'atencao'
  } else if (props.metric === 'turbidity_ntu') {
    if (value > 60) return 'critico'
    if (value >= 40) return 'atencao'
  }
  return 'ok'
}

const meta = computed(() => METRIC_META[props.metric])

const levelText = {
  ok: 'Normal',
  atencao: 'Atenção',
  critico: 'Crítico',
}
</script>

<template>
  <div class="table-scroll">
    <table class="history-table">
      <thead>
        <tr>
          <th>Data e hora</th>
          <th>{{ meta.label }}</th>
          <th>Unidade</th>
          <th>Estado</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.time.toISOString()">
          <td>{{ formatDate(row.time) }}</td>
          <td class="value-cell">
            {{ row.value.toLocaleString('pt-BR', {
              minimumFractionDigits: meta.decimals,
              maximumFractionDigits: meta.decimals
            }) }}
          </td>
          <td>{{ meta.unit }}</td>
          <td>
            <span :class="['table-level', `badge-${levelFor(row.value)}`]">
              {{ levelText[levelFor(row.value)] }}
            </span>
          </td>
        </tr>
        <tr v-if="rows.length === 0">
          <td colspan="4" class="table-empty">Nenhuma leitura no período selecionado.</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
