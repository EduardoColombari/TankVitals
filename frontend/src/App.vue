<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getLatest, getTanks, getThresholds } from './api/client'
import StatusBar from './components/StatusBar.vue'
import MetricCard from './components/MetricCard.vue'
import RangeSelector from './components/RangeSelector.vue'
import HistoryChart from './components/HistoryChart.vue'
import HistoryTable from './components/HistoryTable.vue'
import AlertsPanel from './components/AlertsPanel.vue'
import { useLiveReadings } from './composables/useLiveReadings'
import { useHistory } from './composables/useHistory'
import {
  DEFAULT_THRESHOLDS,
  METRIC_META,
  type HistoryRange,
  type MetricKey,
  type Thresholds,
  type TankInfo,
} from './types'

const tankId = ref('tanque-01')
const tanks = ref<TankInfo[]>([])
const thresholds = ref<Thresholds>(DEFAULT_THRESHOLDS)
const range = ref<HistoryRange>('6h')
const selectedMetric = ref<MetricKey>('temperature_c')

const bootstrapLoading = ref(true)
const bootstrapError = ref<string | null>(null)

const { reading, connection, error: liveError, restart } = useLiveReadings(tankId)
const {
  history,
  stats,
  loading: historyLoading,
  error: historyError,
} = useHistory(tankId, range, selectedMetric)

const metricKeys: MetricKey[] = [
  'temperature_c',
  'ph',
  'level_pct',
  'turbidity_ntu',
]

const currentThreshold = computed(() => thresholds.value[selectedMetric.value])

const selectedStats = computed(() => stats.value?.metrics[selectedMetric.value])

const overallLabel = computed(() => {
  if (!reading.value) return 'Aguardando dados'
  if (!reading.value.online) return 'Sem sinal'
  if (reading.value.level === 'critico') return 'Situação crítica'
  if (reading.value.level === 'atencao') return 'Atenção necessária'
  return 'Tanque estável'
})

const overallClass = computed(() => {
  if (!reading.value?.online) return 'status-neutral'
  return `status-${reading.value.level}`
})

async function bootstrap() {
  bootstrapLoading.value = true
  bootstrapError.value = null

  try {
    const [tankData, thresholdData] = await Promise.all([
      getTanks(),
      getThresholds(),
    ])

    tanks.value = tankData
    thresholds.value = normalizeThresholds(thresholdData)

    if (tankData.length && !tankData.some((tank) => tank.tank_id === tankId.value)) {
      tankId.value = tankData[0].tank_id
    }
  } catch (err) {
    bootstrapError.value =
      err instanceof Error
        ? err.message
        : 'Não foi possível carregar as configurações do dashboard.'

    // O front continua funcional com o contrato conhecido.
    try {
      const latest = await getLatest(tankId.value)
      if (latest) reading.value = latest
    } catch {
      // O erro principal já está sendo mostrado na interface.
    }
  } finally {
    bootstrapLoading.value = false
  }
}

function normalizeThresholds(input: Thresholds): Thresholds {
  const merged: Thresholds = { ...DEFAULT_THRESHOLDS }

  for (const key of metricKeys) {
    if (input[key]) {
      merged[key] = {
        ...DEFAULT_THRESHOLDS[key],
        ...input[key],
      }
    }
  }

  return merged
}

function selectTank(id: string) {
  tankId.value = id
}

function formatNumber(value: number | undefined, decimals = 1): string {
  if (value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

onMounted(() => {
  void bootstrap()
})
</script>

<template>
  <div class="app-shell">
    <StatusBar :reading="reading" :connection="connection" />

    <main class="dashboard">
      <div v-if="bootstrapError" class="global-error">
        <span>⚠</span>
        <div>
          <strong>Backend não disponível</strong>
          <small>{{ bootstrapError }} — tentando manter a interface disponível.</small>
        </div>
        <button type="button" @click="restart">Tentar novamente</button>
      </div>

      <section class="dashboard-toolbar">
        <div>
          <span class="eyebrow">VISÃO GERAL</span>
          <h2>Qualidade da água</h2>
        </div>

        <div class="toolbar-controls">
          <label v-if="tanks.length" class="select-field">
            <span>Tanque</span>
            <select :value="tankId" @change="selectTank(($event.target as HTMLSelectElement).value)">
              <option v-for="tank in tanks" :key="tank.tank_id" :value="tank.tank_id">
                {{ tank.tank_id }}
              </option>
            </select>
          </label>

          <div :class="['overall-status', overallClass]">
            <span class="overall-dot" />
            {{ overallLabel }}
          </div>
        </div>
      </section>

      <section class="metrics-grid" aria-label="Indicadores atuais">
        <MetricCard
          v-for="key in metricKeys"
          :key="key"
          :icon="METRIC_META[key].icon"
          :label="METRIC_META[key].label"
          :metric="reading?.metrics[key]"
          :threshold="thresholds[key]"
        />
      </section>

      <div v-if="liveError" class="inline-warning">
        <span>⚠</span>
        {{ liveError }}
        <span v-if="connection === 'polling'"> O dashboard está usando consulta automática a cada 5 segundos.</span>
      </div>

      <section class="panel chart-panel">
        <div class="panel-header">
          <div>
            <span class="eyebrow">HISTÓRICO</span>
            <h3>Evolução das leituras</h3>
          </div>

          <div class="chart-controls">
            <label class="select-field metric-select">
              <span>Grandeza</span>
              <select v-model="selectedMetric">
                <option v-for="key in metricKeys" :key="key" :value="key">
                  {{ METRIC_META[key].shortLabel }}
                </option>
              </select>
            </label>

            <RangeSelector v-model="range" />
          </div>
        </div>

        <div v-if="historyLoading" class="loading-state">
          <span class="spinner" />
          Carregando histórico...
        </div>

        <div v-else-if="historyError" class="error-state">
          <div class="empty-icon">⚠</div>
          <strong>Não foi possível carregar o histórico</strong>
          <span>{{ historyError }}</span>
        </div>

        <HistoryChart
          v-else
          :history="history"
          :metric="selectedMetric"
          :threshold="currentThreshold"
          :range="range"
        />

        <div v-if="selectedStats" class="stats-strip">
          <div>
            <span>Mínimo</span>
            <strong>{{ formatNumber(selectedStats.min, METRIC_META[selectedMetric].decimals) }}</strong>
          </div>
          <div>
            <span>Média</span>
            <strong>{{ formatNumber(selectedStats.avg, METRIC_META[selectedMetric].decimals) }}</strong>
          </div>
          <div>
            <span>Máximo</span>
            <strong>{{ formatNumber(selectedStats.max, METRIC_META[selectedMetric].decimals) }}</strong>
          </div>
          <div>
            <span>Último</span>
            <strong>{{ formatNumber(selectedStats.last, METRIC_META[selectedMetric].decimals) }}</strong>
          </div>
        </div>
      </section>

      <section class="lower-grid">
        <div class="panel">
          <div class="panel-header compact">
            <div>
              <span class="eyebrow">DADOS</span>
              <h3>Últimas leituras</h3>
            </div>
            <span class="small-note">máximo de 50 registros</span>
          </div>
          <HistoryTable :history="history" :metric="selectedMetric" />
        </div>

        <div class="panel">
          <div class="panel-header compact">
            <div>
              <span class="eyebrow">MONITORAMENTO</span>
              <h3>Alertas do período</h3>
            </div>
            <span class="alert-count">
              {{ history?.series.length ? 'Atualizado' : '—' }}
            </span>
          </div>
          <AlertsPanel :history="history" :thresholds="thresholds" />
        </div>
      </section>

      <footer class="footer">
        <span>TankVitals • IoT para monitoramento de aquicultura</span>
        <span>Vue 3 + TypeScript + Chart.js</span>
      </footer>
    </main>

    <div v-if="bootstrapLoading" class="startup-overlay">
      <div class="startup-card">
        <div class="brand-mark">TV</div>
        <strong>Inicializando TankVitals</strong>
        <span>Conectando ao backend...</span>
        <span class="spinner" />
      </div>
    </div>
  </div>
</template>
