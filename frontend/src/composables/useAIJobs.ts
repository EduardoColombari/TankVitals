import { onMounted, onUnmounted, ref } from 'vue'
import { getDatasets, getJobs, getModels, type Dataset, type Job, type ModelVersion } from '../api/lab'
import { MOCK } from '../config'
import { useLiveStore } from '../stores/live'

export function useAIJobs() {
  const jobs = ref<Job[]>([])
  const datasets = ref<Dataset[]>([])
  const models = ref<ModelVersion[]>([])
  const error = ref('')
  const loading = ref(false)
  let disposed = false
  let timer: ReturnType<typeof setInterval> | undefined
  let unsubscribe: (() => void) | undefined
  async function refresh() {
    if (disposed || loading.value) return
    loading.value = true
    try {
      const result = await Promise.all([getJobs(), getDatasets(), getModels()])
      if (!disposed) [jobs.value, datasets.value, models.value] = result
      error.value = ''
    } catch (cause) {
      if (!disposed) error.value = cause instanceof Error ? cause.message : 'Não foi possível carregar os dados da IA.'
    } finally { loading.value = false }
  }
  onMounted(() => {
    void refresh()
    // Recupera progresso perdido durante desconexões, sem duplicar a conexão WS.
    timer = setInterval(() => void refresh(), MOCK ? 1000 : 5000)
    unsubscribe = useLiveStore().subscribe((event) => {
      if (event.type === 'job_progress') {
        const payload = event.payload
        const job = jobs.value.find((item) => item.id === payload.job_id)
        if (job && typeof payload.progress === 'number') {
          job.progress = Math.max(0, Math.min(1, payload.progress))
          if (typeof payload.message === 'string') job.message = payload.message
          if (payload.status === 'queued' || payload.status === 'running' || payload.status === 'done' || payload.status === 'failed') job.status = payload.status
        }
        if (payload.status === 'done' || payload.status === 'failed' || !job) void refresh()
      }
      if (event.type === 'model_updated') void refresh()
    })
  })
  onUnmounted(() => { disposed = true; clearInterval(timer); unsubscribe?.() })
  return { jobs, datasets, models, error, loading, refresh }
}
