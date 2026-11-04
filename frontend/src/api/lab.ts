import { request } from './http'
import { MOCK } from '../config'
import { mockDatasets, mockGenerate, mockJobs, mockModels, mockTrain } from '../mock/lab'

export interface DatasetParams {
  num_pumps: number; days: number; sensor_count: number; failure_fraction: number
  failure_modes: string[]; noise_scale: number; missing_fraction: number
  absurd_fraction: number; seed: number
}
export interface Job {
  id: string; kind: 'datagen' | 'train'; status: 'queued' | 'running' | 'done' | 'failed'
  progress: number; message: string; created_at: string
}
export interface Dataset { id: string; rows: number; bytes: number; params: DatasetParams; created_at: string }
export interface Scores { mae: number; rmse: number; r2: number }
export type Target = 'health_true' | 'rul_h_true'
export interface ModelVersion {
  version: string; active: boolean; accepted: boolean; created_at: string; reason?: string
  warnings?: string[]
  validation: Record<Target, { model: Scores; baseline: Scores }>
  metrics?: Record<Target, { model: Scores; baseline: Scores }>
  predicted_vs_real?: Record<Target, { actual: number; predicted: number }[]>
  feature_importance?: Record<Target, Record<string, number>>
}
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const getJobs = async () => MOCK ? mockJobs() : request<Job[]>('/api/lab/jobs')
export const getDatasets = async () => MOCK ? mockDatasets() : request<Dataset[]>('/api/lab/datasets')
export const getModels = async () => MOCK ? mockModels() : request<ModelVersion[]>('/api/models')
export const generateDataset = async (params: DatasetParams) => MOCK ? mockGenerate(params) : request<Job>('/api/lab/jobs', post(params))
export const trainModels = async (dataset_id: string) => MOCK ? mockTrain(dataset_id) : request<Job>('/api/models/train', post({ dataset_id }))
