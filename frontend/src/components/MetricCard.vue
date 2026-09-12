<script setup lang="ts">
import { computed } from 'vue'
import type { AlertLevel, MetricThreshold, MetricValue } from '../types'

const props = defineProps<{
  icon: string
  label: string
  metric: MetricValue | undefined
  threshold?: MetricThreshold
}>()

const levelLabel: Record<AlertLevel, string> = {
  ok: 'Normal',
  atencao: 'Atenção',
  critico: 'Crítico',
}

const valueLabel = computed(() => {
  if (!props.metric || props.metric.value === null || Number.isNaN(props.metric.value)) return '—'
  const decimals = props.metric.unit === 'pH' ? 2 : 1
  return props.metric.value.toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
})

const safeRange = computed(() => {
  const t = props.threshold
  if (!t) return 'faixa segura não disponível'

  if (t.safeMax !== undefined && t.safeMin !== undefined) {
    return `ideal: ${format(t.safeMin)}–${format(t.safeMax)} ${t.unit}`
  }

  if (t.safeMax !== undefined) {
    return `ideal: < ${format(t.safeMax)} ${t.unit}`
  }

  if (t.safeMin !== undefined) {
    return `ideal: ≥ ${format(t.safeMin)} ${t.unit}`
  }

  return 'faixa segura não disponível'
})

function format(value: number): string {
  return value.toLocaleString('pt-BR', {
    minimumFractionDigits: props.metric?.unit === 'pH' ? 1 : 0,
    maximumFractionDigits: props.metric?.unit === 'pH' ? 2 : 1,
  })
}
</script>

<template>
  <article :class="['metric-card', `level-${metric?.level ?? 'ok'}`]">
    <div class="metric-head">
      <div class="metric-icon">{{ icon }}</div>
      <span class="metric-label">{{ label }}</span>
      <span v-if="metric" :class="['level-badge', `badge-${metric.level}`]">
        {{ levelLabel[metric.level] }}
      </span>
    </div>

    <div class="metric-value">
      {{ valueLabel }}
      <span v-if="metric" class="metric-unit">{{ metric.unit }}</span>
    </div>

    <div class="metric-footer">
      <span>{{ safeRange }}</span>
      <span v-if="metric" class="metric-indicator" aria-hidden="true" />
    </div>
  </article>
</template>
