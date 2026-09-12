import { ref, watch, type Ref } from 'vue'
import { getHistory, getStats } from '../api/client'
import type { HistoryRange, HistorySeries, MetricKey, Stats } from '../types'

export function useHistory(
  tankId: Ref<string>,
  range: Ref<HistoryRange>,
  metric: Ref<MetricKey>,
) {
  const history = ref<HistorySeries | null>(null)
  const stats = ref<Stats | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  let requestId = 0

  const load = async () => {
    const currentRequest = ++requestId
    loading.value = true
    error.value = null

    try {
      const [historyData, statsData] = await Promise.all([
        getHistory(tankId.value, range.value, metric.value),
        getStats(tankId.value, range.value),
      ])

      if (currentRequest !== requestId) return
      history.value = historyData
      stats.value = statsData
    } catch (err) {
      if (currentRequest !== requestId) return
      history.value = null
      stats.value = null
      error.value = err instanceof Error ? err.message : 'Não foi possível carregar o histórico.'
    } finally {
      if (currentRequest === requestId) loading.value = false
    }
  }

  watch([tankId, range, metric], () => void load(), { immediate: true })

  return {
    history,
    stats,
    loading,
    error,
    reload: load,
  }
}
