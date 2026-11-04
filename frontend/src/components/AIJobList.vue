<script setup lang="ts">
import type { Job } from '../api/lab'
defineProps<{ jobs: Job[] }>()
const statuses = { queued: 'Na fila', running: 'Processando', done: 'Concluído', failed: 'Falhou' }
</script>
<template>
  <section class="panel ai-jobs">
    <h3>Trabalhos recentes</h3>
    <p v-if="!jobs.length">Nenhum trabalho iniciado.</p>
    <article v-for="job in jobs.slice(0, 20)" :key="job.id">
      <div><strong>{{ statuses[job.status] }}</strong><span>{{ Math.round(job.progress * 100) }}%</span></div>
      <progress :value="job.progress" max="1" :aria-label="`Progresso do trabalho ${job.id}`" />
      <p>{{ job.message }}</p>
      <small>{{ new Date(job.created_at).toLocaleString('pt-BR') }} · {{ job.id.slice(0, 8) }}</small>
    </article>
  </section>
</template>
<style scoped>
.ai-jobs { display: grid; gap: 16px; }
article { padding-top: 12px; border-top: 1px solid var(--line-soft); }
article div { display: flex; justify-content: space-between; }
progress { width: 100%; accent-color: var(--accent); }
p, small { color: var(--muted); }
</style>
