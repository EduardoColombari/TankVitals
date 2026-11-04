<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { generateDataset, type DatasetParams } from '../api/lab'
import AIJobList from '../components/AIJobList.vue'
import { useAIJobs } from '../composables/useAIJobs'
import { MOCK } from '../config'
import { useAuthStore } from '../stores/auth'
const auth = useAuthStore()
const { jobs, datasets, error, refresh } = useAIJobs()
const busy = ref(false)
const submissionError = ref('')
const modes = [
  ['rolamento', 'Rolamento gasto'], ['entupimento', 'Filtro entupido'],
  ['superaquecimento', 'Superaquecimento'], ['desbalanceamento', 'Rotor desbalanceado'],
  ['sensor_deriva', 'Sensor com deriva'], ['sensor_travado', 'Sensor travado'],
]
const form = reactive<DatasetParams>({ num_pumps: 100, days: 7, sensor_count: 4, failure_fraction: 0.3,
  failure_modes: modes.map((mode) => mode[0]!), noise_scale: 1, missing_fraction: 0.01, absurd_fraction: 0.001, seed: 42 })
const rows = computed(() => form.num_pumps * form.days * 1440)
const invalid = computed(() => !Number.isFinite(rows.value) || rows.value > 20_000_000 || rows.value <= 0 || (form.failure_fraction > 0 && !form.failure_modes.length))
async function submit() {
  busy.value = true; submissionError.value = ''
  try { await generateDataset(form); await refresh() }
  catch (cause) { submissionError.value = cause instanceof Error ? cause.message : 'Falha ao iniciar geração.' }
  finally { busy.value = false }
}
</script>
<template>
  <main class="dashboard ai-page">
    <header class="page-header"><div><h2>Laboratório</h2><p>Simule a vida das bombas e prepare dados rotulados para treinar a IA.</p></div></header>
    <p v-if="MOCK" class="panel">Modo demonstração: experimente gerar um dataset e acompanhe o progresso simulado. Nenhum arquivo real é criado; os exemplos são apagados ao recarregar a página.</p>
    <p v-if="error || submissionError" role="alert" class="panel">{{ submissionError || error }}</p>
    <div class="ai-columns">
      <form class="panel ai-form" @submit.prevent="submit">
        <h3>Novo dataset</h3>
        <fieldset :disabled="busy || !auth.can('generateData')">
          <div class="ai-fields">
            <label>Bombas<input v-model.number="form.num_pumps" type="number" min="1" max="8333" required /></label>
            <label>Dias simulados<input v-model.number="form.days" type="number" min="1" required /></label>
            <label>Sensores por bomba<select v-model.number="form.sensor_count"><option v-for="n in [4,5,6,7]" :key="n" :value="n">{{ n }}</option></select></label>
            <label>Semente<input v-model.number="form.seed" type="number" min="0" required /></label>
            <label>Fração com falha (0–1)<input v-model.number="form.failure_fraction" type="number" min="0" max="1" step="0.01" required /></label>
            <label>Intensidade do ruído<input v-model.number="form.noise_scale" type="number" min="0" max="10" step="0.1" required /></label>
            <label>Fração faltante (0–1)<input v-model.number="form.missing_fraction" type="number" min="0" max="1" step="0.001" required /></label>
            <label>Fração absurda (0–1)<input v-model.number="form.absurd_fraction" type="number" min="0" max="1" step="0.001" required /></label>
          </div>
          <fieldset class="ai-modes"><legend>Modos de falha</legend><label v-for="mode in modes" :key="mode[0]"><input v-model="form.failure_modes" type="checkbox" :value="mode[0]" />{{ mode[1] }}</label></fieldset>
          <p><strong>{{ rows.toLocaleString('pt-BR') }} leituras</strong> · limite de 20 milhões.</p>
          <p v-if="rows < 1_000_000">Para a entrega, gere pelo menos 1 milhão de leituras.</p>
          <p v-if="invalid" role="alert">Revise o volume e selecione ao menos um modo quando houver falhas.</p>
          <button class="button button-primary" :disabled="invalid || busy">{{ busy ? 'Enviando…' : 'Gerar dataset' }}</button>
        </fieldset>
        <p v-if="!auth.can('generateData')">Apenas administradores podem gerar dados.</p>
      </form>
      <AIJobList :jobs="jobs.filter((job) => job.kind === 'datagen')" />
    </div>
    <section class="panel"><h3>Datasets gerados</h3><p v-if="!datasets.length">Nenhum dataset disponível. O arquivo aparecerá após a geração.</p>
      <div class="ai-table"><table v-if="datasets.length"><thead><tr><th>Dataset</th><th>Leituras</th><th>Bombas / dias</th><th>Tamanho</th><th>Semente</th></tr></thead>
        <tbody><tr v-for="dataset in datasets" :key="dataset.id"><td>{{ dataset.id.slice(0, 8) }}</td><td>{{ dataset.rows.toLocaleString('pt-BR') }}</td><td>{{ dataset.params.num_pumps }} / {{ dataset.params.days }}</td><td>{{ (dataset.bytes / 1048576).toFixed(1) }} MB</td><td>{{ dataset.params.seed }}</td></tr></tbody>
      </table></div>
    </section>
  </main>
</template>
<style src="../ai.css" />
