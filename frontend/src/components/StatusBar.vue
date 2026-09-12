<script setup lang="ts">
import { computed } from 'vue'
import type { ConnectionMode, LatestReading } from '../types'

const props = defineProps<{
  reading: LatestReading | null
  connection: ConnectionMode
}>()

const ageLabel = computed(() => {
  if (!props.reading) return 'aguardando leitura'
  if (props.reading.age_s < 1) return 'agora'
  return `há ${Math.round(props.reading.age_s)} s`
})

const tankLabel = computed(() => props.reading?.tank_id ?? 'tanque-01')

const deviceLabel = computed(() => props.reading?.device_id ?? 'dispositivo aguardando')

const statusText = computed(() => {
  if (!props.reading?.online) return 'Offline'
  return 'Online'
})

const connectionText = computed(() => {
  if (props.connection === 'websocket') return 'Tempo real'
  if (props.connection === 'polling') return 'Polling • 5 s'
  return 'Desconectado'
})
</script>

<template>
  <section class="status-bar">
    <div class="status-title">
      <div class="brand-mark">TV</div>
      <div>
        <span class="eyebrow">TANKVITALS</span>
        <h1>Monitoramento do tanque</h1>
      </div>
    </div>

    <div class="status-items">
      <div class="status-item">
        <span class="label">Tanque</span>
        <strong>{{ tankLabel }}</strong>
      </div>

      <div class="status-item">
        <span class="label">Dispositivo</span>
        <strong>{{ deviceLabel }}</strong>
      </div>

      <div class="status-item">
        <span class="label">Estado</span>
        <strong :class="['status-pill', reading?.online ? 'is-online' : 'is-offline']">
          <span class="dot" /> {{ statusText }}
        </strong>
      </div>

      <div class="status-item">
        <span class="label">Última leitura</span>
        <strong>{{ ageLabel }}</strong>
      </div>

      <div class="live-badge" :class="`mode-${connection}`">
        <span class="live-dot" />
        {{ connectionText }}
      </div>
    </div>
  </section>
</template>
