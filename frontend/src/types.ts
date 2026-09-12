export type AlertLevel = 'ok' | 'atencao' | 'critico'
export type ConnectionMode = 'websocket' | 'polling' | 'disconnected'

export type MetricKey =
  | 'temperature_c'
  | 'ph'
  | 'level_pct'
  | 'turbidity_ntu'

export interface MetricValue {
  value: number
  level: AlertLevel
  unit: string
}

export interface LatestReading {
  tank_id: string
  device_id: string
  time: string
  age_s: number
  online: boolean
  level: AlertLevel
  metrics: Partial<Record<MetricKey, MetricValue>>
}

export interface HistoryPoint {
  t: string
  v: number
}

export interface HistorySeriesItem {
  metric: MetricKey
  unit: string
  points: HistoryPoint[]
}

export interface HistorySeries {
  tank_id: string
  range: HistoryRange
  window: string
  series: HistorySeriesItem[]
}

export interface StatsMetric {
  min: number
  max: number
  avg: number
  last: number
}

export interface Stats {
  tank_id: string
  range: HistoryRange
  count: number
  metrics: Partial<Record<MetricKey, StatsMetric>>
}

export interface ThresholdRange {
  min?: number
  max?: number
  unit?: string
}

export interface MetricThreshold {
  criticalBelow?: number
  warningBelow?: number
  safeMin?: number
  safeMax?: number
  warningAbove?: number
  criticalAbove?: number
  unit: string
  label: string
}

export type Thresholds = Partial<Record<MetricKey, MetricThreshold>>

export interface TankInfo {
  tank_id: string
  device_id?: string
  last_seen?: string
  online: boolean
}

export interface HealthResponse {
  status: string
  mqtt?: string
  influxdb?: string
  [key: string]: unknown
}

export interface LiveMessage {
  type: 'reading' | 'status'
  payload: LatestReading | { tank_id: string; online: boolean }
}

export type HistoryRange = '1h' | '6h' | '24h' | '7d'

export const METRIC_META: Record<MetricKey, {
  label: string
  shortLabel: string
  unit: string
  decimals: number
  icon: string
}> = {
  temperature_c: {
    label: 'Temperatura da água',
    shortLabel: 'Temperatura',
    unit: '°C',
    decimals: 1,
    icon: '🌡️',
  },
  ph: {
    label: 'pH',
    shortLabel: 'pH',
    unit: 'pH',
    decimals: 2,
    icon: '🧪',
  },
  level_pct: {
    label: 'Nível da água',
    shortLabel: 'Nível',
    unit: '%',
    decimals: 1,
    icon: '💧',
  },
  turbidity_ntu: {
    label: 'Turbidez',
    shortLabel: 'Turbidez',
    unit: 'NTU',
    decimals: 1,
    icon: '🔬',
  },
}

export const DEFAULT_THRESHOLDS: Thresholds = {
  temperature_c: {
    criticalBelow: 22,
    warningBelow: 24,
    safeMin: 24,
    safeMax: 28,
    warningAbove: 28,
    criticalAbove: 30,
    unit: '°C',
    label: 'Temperatura',
  },
  ph: {
    criticalBelow: 6,
    warningBelow: 6.5,
    safeMin: 6.5,
    safeMax: 8,
    warningAbove: 8,
    criticalAbove: 8.5,
    unit: 'pH',
    label: 'pH',
  },
  level_pct: {
    criticalBelow: 15,
    warningBelow: 30,
    safeMin: 30,
    safeMax: 100,
    unit: '%',
    label: 'Nível',
  },
  turbidity_ntu: {
    safeMin: 0,
    safeMax: 40,
    warningAbove: 40,
    criticalAbove: 60,
    unit: 'NTU',
    label: 'Turbidez',
  },
}
