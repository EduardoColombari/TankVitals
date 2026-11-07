<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { AlertTriangle, RefreshCw, Workflow } from 'lucide-vue-next'
import { ApiError, request } from '../api/http'

// Shape follows the provisional GET /api/messaging example in ARQUITETURA.md.
// The response contract and the endpoint itself still require BACK/WEB/PLAT approval.
interface QueueMetric {
  name: string
  messages_ready: number
  messages_unacknowledged: number
  publish_rate: number
  deliver_rate: number
  consumers: number
}

interface RecentMessage {
  routing_key: string
  message: Record<string, unknown>
}

interface MessagingSnapshot {
  queues: QueueMetric[]
  recent_messages: RecentMessage[]
  dlq: { messages: number; sample?: unknown[] }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isNonNegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function isMessagingSnapshot(value: unknown): value is MessagingSnapshot {
  if (!isRecord(value) || !Array.isArray(value.queues) || !Array.isArray(value.recent_messages) || !isRecord(value.dlq)) {
    return false
  }
  const queuesValid = value.queues.every((queue) =>
    isRecord(queue) &&
    typeof queue.name === 'string' && queue.name.length > 0 &&
    isNonNegativeInteger(queue.messages_ready) &&
    isNonNegativeInteger(queue.messages_unacknowledged) &&
    isNonNegativeNumber(queue.publish_rate) &&
    isNonNegativeNumber(queue.deliver_rate) &&
    isNonNegativeInteger(queue.consumers),
  )
  const messagesValid = value.recent_messages.every((entry) =>
    isRecord(entry) && typeof entry.routing_key === 'string' && entry.routing_key.length > 0 && isRecord(entry.message),
  )
  return queuesValid && messagesValid &&
    isNonNegativeInteger(value.dlq.messages) &&
    (value.dlq.sample === undefined || Array.isArray(value.dlq.sample))
}

const snapshot = ref<MessagingSnapshot | null>(null)
const error = ref('')
const loading = ref(true)
const lastUpdated = ref<Date | null>(null)
const queueHistory = ref<Record<string, number[]>>({})
const POLL_INTERVAL_MS = 2000
let timer: number | undefined
let active = false
let requestInFlight = false

const requestState = computed(() => {
  if (loading.value && !snapshot.value) return 'Carregando métricas do RabbitMQ…'
  if (error.value && !snapshot.value) return 'Métricas indisponíveis'
  if (error.value) return 'Atualização indisponível; exibindo a última resposta.'
  return 'Atualização automática a cada 2 s.'
})

async function refresh(): Promise<void> {
  if (requestInFlight || !active) return
  requestInFlight = true
  loading.value = !snapshot.value
  try {
    const data = await request<unknown>('/api/messaging')
    if (!isMessagingSnapshot(data)) {
      throw new Error('Resposta fora do contrato provisório de mensageria.')
    }
    snapshot.value = data
    for (const queue of data.queues) {
      const points = queueHistory.value[queue.name] ?? []
      queueHistory.value[queue.name] = [...points, queue.messages_ready].slice(-30)
    }
    lastUpdated.value = new Date()
    error.value = ''
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Não foi possível consultar o endpoint de mensageria.'
  } finally {
    loading.value = false
    requestInFlight = false
  }
}

function sparkline(points: number[]): string {
  if (!points.length) return ''
  const maximum = Math.max(1, ...points)
  return points.map((value, index) => `${(index / Math.max(1, points.length - 1)) * 100},${28 - (value / maximum) * 24}`).join(' ')
}

function displayTime(value: Date | null): string {
  return value ? value.toLocaleTimeString('pt-BR') : '—'
}

function formatMessage(message: Record<string, unknown>): string {
  return JSON.stringify(message, null, 2)
}

onMounted(() => {
  active = true
  void refresh()
  timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS)
})
onUnmounted(() => {
  active = false
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <main class="dashboard messaging-page">
    <header class="page-header">
      <div>
        <h2>Mensageria</h2>
        <p>Filas, consumidores, taxas de entrega e mensagens encaminhadas pelo RabbitMQ.</p>
      </div>
      <button type="button" class="button" :disabled="loading" @click="refresh">
        <RefreshCw :size="16" aria-hidden="true" />
        Atualizar
      </button>
    </header>

    <p class="messaging-state" role="status" aria-live="polite">
      <AlertTriangle v-if="error" :size="17" aria-hidden="true" />
      <Workflow v-else :size="17" aria-hidden="true" />
      {{ requestState }}
      <span v-if="lastUpdated">Última resposta: {{ displayTime(lastUpdated) }}</span>
    </p>

    <section v-if="error && !snapshot" class="panel messaging-empty">
      <h3>Não foi possível carregar os dados</h3>
      <p>{{ error }}</p>
      <p>O endpoint <code>GET /api/messaging</code> ainda depende da implementação da API e da aprovação do contrato pelas frentes BACK, WEB e PLAT.</p>
    </section>

    <template v-if="snapshot">
      <section class="panel messaging-queues">
        <div class="panel-header">
          <h3>Filas</h3>
          <span>{{ snapshot.queues.length }} fila(s)</span>
        </div>
        <div class="table-scroll">
          <table class="history-table">
            <thead>
              <tr><th>Fila</th><th>Prontas</th><th>Não confirmadas</th><th>Publicação/s</th><th>Entrega/s</th><th>Consumidores</th><th>Histórico local</th></tr>
            </thead>
            <tbody>
              <tr v-for="queue in snapshot.queues" :key="queue.name">
                <td><code>{{ queue.name }}</code></td>
                <td>{{ queue.messages_ready }}</td>
                <td>{{ queue.messages_unacknowledged }}</td>
                <td>{{ queue.publish_rate.toFixed(2) }}</td>
                <td>{{ queue.deliver_rate.toFixed(2) }}</td>
                <td>{{ queue.consumers }}</td>
                <td>
                  <svg v-if="queueHistory[queue.name]?.length" class="queue-sparkline" viewBox="0 0 100 30" role="img" :aria-label="`Histórico local da fila ${queue.name}`">
                    <polyline :points="sparkline(queueHistory[queue.name] ?? [])" />
                  </svg>
                  <span v-else>Sem amostras</span>
                </td>
              </tr>
              <tr v-if="!snapshot.queues.length"><td colspan="7">Nenhuma fila retornada.</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="messaging-columns">
        <article class="panel">
          <div class="panel-header"><h3>Mensagens recentes</h3></div>
          <p v-if="!snapshot.recent_messages.length" class="small-note">Nenhuma mensagem recente disponível.</p>
          <div v-else class="message-list">
            <details v-for="(item, index) in snapshot.recent_messages" :key="`${item.routing_key}-${index}`">
              <summary><code>{{ item.routing_key }}</code></summary>
              <pre>{{ formatMessage(item.message) }}</pre>
            </details>
          </div>
          <p class="small-note">A tela exibe a quantidade recebida pela API; armazenamento e retenção ainda dependem do contrato aprovado.</p>
        </article>

        <article class="panel">
          <div class="panel-header"><h3>Dead-letter queue</h3><strong>{{ snapshot.dlq.messages }}</strong></div>
          <p v-if="!snapshot.dlq.sample?.length" class="small-note">Amostra indisponível ou vazia.</p>
          <pre v-else>{{ JSON.stringify(snapshot.dlq.sample, null, 2) }}</pre>
          <p class="small-note">Amostra sujeita à permissão de leitura e à aprovação do contrato da API.</p>
        </article>
      </section>
    </template>
  </main>
</template>

<style scoped>
.messaging-page { display: grid; gap: 16px; }
.messaging-state { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; color: var(--muted); font-weight: var(--fw-bold); }
.messaging-state span { margin-left: auto; font-size: .8125rem; }
.messaging-empty { display: grid; gap: 10px; color: var(--muted); }
.messaging-empty h3 { color: var(--ink); }
.messaging-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); gap: 16px; }
.messaging-columns .panel { min-width: 0; }
.message-list { display: grid; gap: 8px; max-height: 480px; overflow: auto; }
.message-list details { padding: 10px; border: var(--bw) solid var(--line-soft); border-radius: var(--radius-sm); }
.message-list pre, .messaging-columns pre { max-height: 280px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .8125rem; }
.small-note { margin-top: 10px; color: var(--muted); font-size: .8125rem; }
.queue-sparkline { width: 100px; height: 30px; overflow: visible; }
.queue-sparkline polyline { fill: none; stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
@media (max-width: 900px) { .messaging-queues .table-scroll { overflow-x: auto; } .messaging-queues table { min-width: 760px; } }
</style>
