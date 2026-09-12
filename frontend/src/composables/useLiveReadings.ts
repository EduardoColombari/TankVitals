import { onUnmounted, ref, watch, type Ref } from 'vue'
import { getLatest, getWebSocketUrl } from '../api/client'
import type { LatestReading, LiveMessage, ConnectionMode } from '../types'

export function useLiveReadings(tankId: Ref<string>) {
  const reading = ref<LatestReading | null>(null)
  const connection = ref<ConnectionMode>('disconnected')
  const error = ref<string | null>(null)

  let socket: WebSocket | null = null
  let retryTimer: number | null = null
  let pollTimer: number | null = null
  let retryAttempt = 0
  let stopped = false
  let failures = 0

  const clearRetry = () => {
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  const stopPolling = () => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  const startPolling = () => {
    if (pollTimer !== null) return
    connection.value = 'polling'
    void poll()
    pollTimer = window.setInterval(() => void poll(), 5000)
  }

  const poll = async () => {
    try {
      const data = await getLatest(tankId.value)
      reading.value = data
      error.value = null
      connection.value = 'polling'
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Falha ao consultar o backend.'
    }
  }

  const scheduleReconnect = () => {
    if (stopped || retryTimer !== null) return
    const delay = Math.min(1000 * 2 ** retryAttempt, 30000)
    retryAttempt += 1
    retryTimer = window.setTimeout(() => {
      retryTimer = null
      connect()
    }, delay)
  }

  const connect = () => {
    if (stopped || !tankId.value) return

    clearRetry()
    stopPolling()

    try {
      socket?.close()
      socket = new WebSocket(getWebSocketUrl(tankId.value))
      connection.value = 'disconnected'

      socket.onopen = () => {
        retryAttempt = 0
        failures = 0
        connection.value = 'websocket'
        error.value = null
      }

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const message = JSON.parse(event.data) as LiveMessage
          if (message.type === 'reading') {
            reading.value = message.payload as LatestReading
            error.value = null
          } else if (message.type === 'status') {
            const status = message.payload as { tank_id: string; online: boolean }
            if (reading.value && status.tank_id === reading.value.tank_id) {
              reading.value = { ...reading.value, online: status.online }
            }
          }
        } catch {
          error.value = 'O backend enviou uma mensagem inválida.'
        }
      }

      socket.onerror = () => {
        failures += 1
        error.value = 'Conexão ao vivo indisponível.'
      }

      socket.onclose = () => {
        socket = null
        connection.value = 'disconnected'
        if (stopped) return

        if (failures >= 2) {
          startPolling()
        }
        scheduleReconnect()
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Não foi possível conectar.'
      failures += 1
      if (failures >= 2) startPolling()
      scheduleReconnect()
    }
  }

  const restart = () => {
    failures = 0
    retryAttempt = 0
    clearRetry()
    stopPolling()
    reading.value = null
    connect()
  }

  watch(tankId, restart, { immediate: true })

  onUnmounted(() => {
    stopped = true
    clearRetry()
    stopPolling()
    socket?.close()
    socket = null
  })

  return {
    reading,
    connection,
    error,
    restart,
  }
}
