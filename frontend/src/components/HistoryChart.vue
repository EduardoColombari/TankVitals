<script setup lang="ts">
import { computed } from 'vue'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  TimeScale,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import { Line } from 'vue-chartjs'
import type { ChartData, ChartOptions } from 'chart.js'
import type { HistorySeries, MetricKey, MetricThreshold } from '../types'
import { METRIC_META } from '../types'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  TimeScale,
  Tooltip,
  Legend,
  Filler,
)

const props = defineProps<{
  history: HistorySeries | null
  metric: MetricKey
  threshold?: MetricThreshold
  range: string
}>()

const meta = computed(() => METRIC_META[props.metric])

const currentSeries = computed(() =>
  props.history?.series.find((item) => item.metric === props.metric),
)

const labels = computed(() => currentSeries.value?.points.map((point) => new Date(point.t)) ?? [])

const safeMin = computed(() => props.threshold?.safeMin)
const safeMax = computed(() => props.threshold?.safeMax)

const chartData = computed<ChartData<'line', number[], Date>>(() => {
  const points = currentSeries.value?.points ?? []
  const datasets: ChartData<'line', number[], Date>['datasets'] = [
    {
      label: meta.value.label,
      data: points.map((point) => point.v),
      borderWidth: 2.5,
      borderColor: '#157f78',
      backgroundColor: 'rgba(21, 127, 120, 0.08)',
      pointRadius: points.length > 120 ? 0 : 2,
      pointHoverRadius: 4,
      tension: 0.25,
      fill: true,
    },
  ]

  if (safeMin.value !== undefined) {
    datasets.push({
      label: 'Limite mínimo',
      data: points.map(() => safeMin.value as number),
      borderWidth: 1,
      borderDash: [6, 5],
      borderColor: '#f59e0b',
      pointRadius: 0,
      fill: false,
    })
  }

  if (safeMax.value !== undefined) {
    datasets.push({
      label: 'Limite máximo',
      data: points.map(() => safeMax.value as number),
      borderWidth: 1,
      borderDash: [6, 5],
      borderColor: '#f59e0b',
      pointRadius: 0,
      fill: false,
    })
  }

  return {
    labels: labels.value,
    datasets,
  }
})

const chartOptions = computed<ChartOptions<'line'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: {
    duration: 250,
  },
  interaction: {
    intersect: false,
    mode: 'index',
  },
  plugins: {
    legend: {
      display: false,
    },
    tooltip: {
      callbacks: {
        label: (context) => {
          const value = context.parsed.y
          if (value === null) return ''
          return `${context.dataset.label}: ${value.toLocaleString('pt-BR')} ${meta.value.unit}`
        },
      },
    },
  },
  scales: {
    x: {
      type: 'time',
      time: {
        unit: props.range === '7d' ? 'day' : props.range === '24h' ? 'hour' : 'minute',
        displayFormats: {
          minute: 'HH:mm',
          hour: props.range === '7d' ? 'dd/MM' : 'HH:mm',
          day: 'dd/MM',
        },
      },
      grid: {
        display: false,
      },
      ticks: {
        maxTicksLimit: 8,
      },
    },
    y: {
      beginAtZero: false,
      grid: {
        color: 'rgba(15, 23, 42, 0.07)',
      },
      ticks: {
        callback: (value) => `${value} ${meta.value.unit}`,
      },
    },
  },
}))
</script>

<template>
  <div class="chart-wrap">
    <div v-if="!currentSeries || currentSeries.points.length === 0" class="chart-empty">
      <div class="empty-icon">◌</div>
      <strong>Sem dados no período</strong>
      <span>Assim que houver leituras, elas aparecerão neste gráfico.</span>
    </div>

    <Line v-else :data="chartData" :options="chartOptions" />
  </div>
</template>
