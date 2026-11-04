<script setup lang="ts">
import { computed, ref } from 'vue'
import { Chart as ChartJS, LinearScale, CategoryScale, PointElement, LineElement, BarElement, Tooltip, Legend, Title } from 'chart.js'
import { Bar, Scatter } from 'vue-chartjs'
import { trainModels, type Target } from '../api/lab'
import AIJobList from '../components/AIJobList.vue'
import { useAIJobs } from '../composables/useAIJobs'
import { MOCK } from '../config'
import { useAuthStore } from '../stores/auth'
ChartJS.register(LinearScale, CategoryScale, PointElement, LineElement, BarElement, Tooltip, Legend, Title)
const auth = useAuthStore()
const { jobs, datasets, models, error, refresh } = useAIJobs()
const datasetId = ref('')
const selectedVersion = ref('')
const target = ref<Target>('health_true')
const busy = ref(false)
const submissionError = ref('')
const selected = computed(() => models.value.find((model) => model.version === selectedVersion.value) ?? models.value.find((model) => model.active) ?? models.value[0])
const scores = computed(() => (selected.value?.metrics ?? selected.value?.validation)?.[target.value])
const limit = computed(() => target.value === 'health_true' ? 100 : 168)
const scatter = computed(() => ({ datasets: [
  { label: 'Previsto × real', data: (selected.value?.predicted_vs_real?.[target.value] ?? []).map((point) => ({ x: point.actual, y: point.predicted })), backgroundColor: '#288ca0', pointRadius: 3 },
  { label: 'Referência ideal', data: [{ x: 0, y: 0 }, { x: limit.value, y: limit.value }], showLine: true, borderColor: '#667085', pointRadius: 0 },
] }))
const scatterOptions = computed(() => ({ responsive: true, maintainAspectRatio: false, scales: {
  x: { min: 0, max: limit.value, title: { display: true, text: 'Valor real' } },
  y: { min: 0, max: limit.value, title: { display: true, text: 'Valor previsto' } },
} }))
const importance = computed(() => {
  const entries = Object.entries(selected.value?.feature_importance?.[target.value] ?? {}).sort((a, b) => b[1] - a[1])
  return { labels: entries.map(([name]) => name), datasets: [{ label: 'Importância', data: entries.map(([, value]) => value), backgroundColor: '#288ca0' }] }
})
async function submit() {
  busy.value = true; submissionError.value = ''
  try { await trainModels(datasetId.value); await refresh() }
  catch (cause) { submissionError.value = cause instanceof Error ? cause.message : 'Falha ao iniciar treino.' }
  finally { busy.value = false }
}
</script>
<template>
  <main class="dashboard ai-page">
    <header class="page-header"><div><h2>Treinamento</h2><p>Saúde e vida útil restante, avaliadas em bombas que não participaram do treino.</p></div></header>
    <p v-if="MOCK" class="panel">Modo demonstração: jobs, versões, métricas e gráficos são exemplos para testar a interface. Nenhum modelo real é treinado. Gere primeiro um dataset no Laboratório.</p>
    <p v-if="error || submissionError" role="alert" class="panel">{{ submissionError || error }}</p>
    <div class="ai-columns">
      <form class="panel ai-form" @submit.prevent="submit"><h3>Treinar modelos</h3>
        <label>Dataset<select v-model="datasetId" required :disabled="busy"><option value="" disabled>Selecione um dataset</option><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.id.slice(0, 8) }} · {{ dataset.rows.toLocaleString('pt-BR') }} leituras</option></select></label>
        <p>Separação por bomba: 60% treino, 20% validação e 20% teste. Janela de 30 leituras. A versão só é aprovada quando os dois modelos superam a previsão pela média na validação.</p>
        <button v-if="auth.can('train')" class="button button-primary" :disabled="busy || !datasetId">{{ busy ? 'Enviando…' : 'Iniciar treinamento' }}</button>
        <p v-else>Apenas administradores podem iniciar treinos.</p>
      </form>
      <AIJobList :jobs="jobs.filter((job) => job.kind === 'train')" />
    </div>
    <section class="panel"><h3>Versões</h3><p v-if="!models.length">Nenhum treinamento concluído.</p>
      <div class="ai-table"><table v-if="models.length"><thead><tr><th>Versão</th><th>Data</th><th>Resultado</th><th>Detalhes</th></tr></thead><tbody>
        <tr v-for="model in models" :key="model.version"><td>{{ model.version.slice(0, 10) }}</td><td>{{ new Date(model.created_at).toLocaleString('pt-BR') }}</td><td>{{ model.active ? 'Em uso' : model.accepted ? 'Aprovada' : 'Não superou a base' }}</td><td><button class="button" @click="selectedVersion = model.version">Ver métricas</button></td></tr>
      </tbody></table></div>
    </section>
    <section v-if="selected && scores" class="panel">
      <h3>Avaliação · {{ selected.version.slice(0, 10) }} {{ selected.active ? '· em uso' : '' }}</h3>
      <label>Modelo <select v-model="target"><option value="health_true">Saúde (0–100)</option><option value="rul_h_true">Vida útil restante (horas)</option></select></label>
      <p>{{ selected.metrics ? 'Conjunto de teste reservado.' : 'Validação: versão reprovada, sem promoção para uso.' }} MAE e RMSE menores são melhores; R² maior é melhor.</p>
      <p v-if="selected.reason">{{ selected.reason }}</p><p v-for="warning in selected.warnings" :key="warning">{{ warning }}</p>
      <div class="ai-table"><table><thead><tr><th>Métrica</th><th>Modelo</th><th>Base: média</th></tr></thead><tbody><tr v-for="metric in (['mae','rmse','r2'] as const)" :key="metric"><th>{{ metric.toUpperCase() }}</th><td>{{ scores.model[metric].toFixed(3) }}</td><td>{{ scores.baseline[metric].toFixed(3) }}</td></tr></tbody></table></div>
    </section>
    <div v-if="selected?.predicted_vs_real" class="ai-columns">
      <section class="panel"><h3>Previsto × real</h3><div class="ai-chart"><Scatter :data="scatter" :options="scatterOptions" /></div></section>
      <section class="panel"><h3>Importância dos atributos</h3><div class="ai-chart"><Bar :data="importance" :options="{ responsive: true, maintainAspectRatio: false, indexAxis: 'y' }" /></div></section>
    </div>
  </main>
</template>
<style src="../ai.css" />
