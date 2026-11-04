/** Demonstração da interface: não gera Parquet nem treina modelos. */
import type { Dataset, DatasetParams, Job, ModelVersion, Target } from '../api/lab'

const jobs: Job[] = []
const datasets: Dataset[] = []
const models: ModelVersion[] = []
const pending = new Map<string, () => void>()

export function mockJobs(): Job[] {
  for (const job of jobs) {
    if (job.status === 'done') continue
    job.progress = Math.min(1, (Date.now() - Date.parse(job.created_at)) / 12000)
    job.status = job.progress === 1 ? 'done' : 'running'
    job.message = job.status === 'done' ? 'Demonstração concluída' : 'Simulando o progresso da interface…'
    if (job.status === 'done') { pending.get(job.id)?.(); pending.delete(job.id) }
  }
  return structuredClone(jobs)
}
export const mockDatasets = () => structuredClone(datasets)
export const mockModels = () => structuredClone(models)

function enqueue(kind: Job['kind'], complete: () => void): Job {
  const job: Job = { id: crypto.randomUUID(), kind, status: 'queued', progress: 0,
    message: 'Demonstração na fila', created_at: new Date().toISOString() }
  jobs.unshift(job)
  pending.set(job.id, complete)
  return { ...job }
}

export function mockGenerate(params: DatasetParams): Job {
  const rows = params.num_pumps * params.days * 1440
  if (!Number.isFinite(rows) || rows < 1 || rows > 20_000_000) throw new Error('Volume inválido; limite de 20 milhões de leituras.')
  const snapshot = JSON.parse(JSON.stringify(params)) as DatasetParams
  return enqueue('datagen', () => datasets.unshift({ id: crypto.randomUUID(), params: snapshot,
    rows, bytes: rows * 32, created_at: new Date().toISOString() }))
}

export function mockTrain(datasetId: string): Job {
  if (!datasets.some((dataset) => dataset.id === datasetId)) throw new Error('Selecione um dataset da demonstração.')
  return enqueue('train', () => {
    models.forEach((model) => { model.active = false })
    const scores = {
      health_true: { model: { mae: 8.2, rmse: 11.4, r2: 0.79 }, baseline: { mae: 29.5, rmse: 34.2, r2: -0.01 } },
      rul_h_true: { model: { mae: 19.3, rmse: 27.1, r2: 0.68 }, baseline: { mae: 55.4, rmse: 66.2, r2: -0.02 } },
    }
    const points = (target: Target) => Array.from({ length: 60 }, (_, index) => {
      const limit = target === 'health_true' ? 100 : 168
      const actual = index / 59 * limit
      return { actual, predicted: Math.max(0, Math.min(limit, actual + Math.sin(index * 2.3) * limit * 0.17)) }
    })
    models.unshift({ version: `demo-${models.length + 1}`, active: true, accepted: true,
      created_at: new Date().toISOString(), validation: scores, metrics: scores,
      warnings: ['Valores ilustrativos: estes números não são resultados de treinamento.'],
      predicted_vs_real: { health_true: points('health_true'), rul_h_true: points('rul_h_true') },
      feature_importance: {
        health_true: { vibration_mm_s_mean: 0.38, motor_temp_c_mean: 0.29, flow_l_min_mean: 0.19, current_a_mean: 0.14 },
        rul_h_true: { vibration_mm_s_trend: 0.35, motor_temp_c_trend: 0.28, flow_l_min_mean: 0.22, current_a_std: 0.15 },
      },
    })
  })
}
