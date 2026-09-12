import type {
  HealthResponse,
  HistoryRange,
  HistorySeries,
  LatestReading,
  Stats,
  Thresholds,
  TankInfo,
} from '../types'

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? ''
const API_BASE = configuredBase.replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let detail = `Erro HTTP ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // Mantém a mensagem HTTP quando a resposta não for JSON.
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/api/health')
}

export function getThresholds(): Promise<Thresholds> {
  return request<Thresholds>('/api/thresholds')
}

export function getTanks(): Promise<TankInfo[]> {
  return request<TankInfo[]>('/api/tanks')
}

export function getLatest(tankId: string): Promise<LatestReading> {
  return request<LatestReading>(
    `/api/readings/latest?tank_id=${encodeURIComponent(tankId)}`,
  )
}

export function getHistory(
  tankId: string,
  range: HistoryRange,
  metric?: string,
): Promise<HistorySeries> {
  const params = new URLSearchParams({
    tank_id: tankId,
    range,
  })
  if (metric) params.set('metrics', metric)
  return request<HistorySeries>(`/api/readings/history?${params.toString()}`)
}

export function getStats(tankId: string, range: HistoryRange): Promise<Stats> {
  const params = new URLSearchParams({
    tank_id: tankId,
    range,
  })
  return request<Stats>(`/api/stats?${params.toString()}`)
}

export function getWebSocketUrl(tankId: string): string {
  if (!API_BASE) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}/ws/live?tank_id=${encodeURIComponent(tankId)}`
  }

  const url = new URL(API_BASE, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = `${url.pathname.replace(/\/$/, '')}/ws/live`
  url.search = `?tank_id=${encodeURIComponent(tankId)}`
  return url.toString()
}
