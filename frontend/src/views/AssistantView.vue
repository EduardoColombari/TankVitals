<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { assistantStatus, askAssistant, type AssistantStatus } from '../api/assistant'
import { renderAssistantMarkdown } from '../assistantMarkdown'
import questions from '../assistantQuestions.json'
import { MOCK } from '../config'

const question = ref('')
const messages = ref<{ role: 'user' | 'assistant'; text: string }[]>([])
const status = ref<AssistantStatus | null>(null)
const error = ref('')
const statusError = ref('')
const busy = ref(false)
const end = ref<HTMLElement | null>(null)
const statusAbort = new AbortController()
let active: AbortController | null = null
let poll: ReturnType<typeof setTimeout> | undefined
let disposed = false
function stop() { active?.abort() }

async function refreshStatus() {
  if (MOCK || disposed) return
  try { status.value = await assistantStatus(statusAbort.signal); statusError.value = '' }
  catch (cause) { if (!disposed) statusError.value = cause instanceof Error ? cause.message : 'Falha ao consultar modelo.' }
  finally { if (!disposed) poll = setTimeout(refreshStatus, 5000) }
}

async function send(text = question.value) {
  if (!text.trim() || busy.value || MOCK) return
  question.value = ''
  error.value = ''
  busy.value = true
  messages.value.push({ role: 'user', text: text.trim() }, { role: 'assistant', text: '' })
  const index = messages.value.length - 1
  active = new AbortController()
  try {
    await askAssistant(text.trim(), (token) => {
      messages.value[index]!.text += token
      void nextTick(() => end.value?.scrollIntoView({ block: 'nearest' }))
    }, active.signal)
  } catch (cause) {
    if (!disposed) error.value = active.signal.aborted ? 'Resposta interrompida por você.' : cause instanceof Error ? cause.message : 'Não foi possível responder.'
  } finally { busy.value = false; active = null }
}

onMounted(refreshStatus)
onUnmounted(() => { disposed = true; clearTimeout(poll); statusAbort.abort(); active?.abort() })
</script>

<template>
  <main class="dashboard assistant-page">
    <header class="page-header"><div><h2>Assistente</h2><p>Pergunte sobre os dados dos tanques e a arquitetura do TankVitals.</p></div></header>
    <div class="panel assistant-status" role="status">
      <template v-if="MOCK">O assistente precisa da API real. Desative VITE_MOCK e entre com um usuário do backend.</template>
      <template v-else-if="statusError">{{ statusError }}</template>
      <template v-else-if="!status">Consultando o modelo…</template>
      <template v-else-if="!status.available">Ollama indisponível. Verifique o serviço local.</template>
      <template v-else-if="!status.ready">Carregando o modelo {{ status.model }}…</template>
      <template v-else>Modelo local pronto · {{ status.model }}</template>
    </div>
    <section class="panel assistant-chat" aria-label="Conversa com o assistente" :aria-busy="busy">
      <p v-if="!messages.length" class="assistant-empty">Como posso ajudar? Escolha uma pergunta ou escreva a sua.</p>
      <article v-for="(message, index) in messages" :key="index" class="assistant-message" :class="message.role">
        <strong>{{ message.role === 'user' ? 'Você' : 'TankVitals' }}</strong>
        <p v-if="message.role === 'user'" class="user-text">{{ message.text }}</p>
        <!-- Única entrada de v-html: markdown-it com HTML e imagens desabilitados. -->
        <div v-else class="assistant-markdown" v-html="renderAssistantMarkdown(message.text || 'Pensando…')" />
      </article>
      <span ref="end" />
    </section>
    <p v-if="error" role="alert" class="panel">{{ error }}</p>
    <div class="assistant-suggestions" aria-label="Sugestões de perguntas">
      <button v-for="item in questions" :key="item.question" type="button" :disabled="busy || MOCK || !status?.available" @click="send(item.question)">{{ item.question }}</button>
    </div>
    <form class="panel assistant-form" @submit.prevent="send()">
      <label for="assistant-question">Sua pergunta</label>
      <textarea id="assistant-question" v-model="question" maxlength="2000" rows="3" required :disabled="busy || MOCK" placeholder="Ex.: qual bomba precisa de atenção?" />
      <div class="assistant-actions"><small>Cada pergunta usa um resumo atualizado. Limite de 5 por minuto.</small>
        <button v-if="busy" type="button" @click="stop">Interromper</button>
        <button v-else type="submit" :disabled="!question.trim() || MOCK || !status?.available">Enviar pergunta</button>
      </div>
    </form>
  </main>
</template>

<style scoped>
.assistant-page { display: grid; gap: 1rem; max-width: 1050px; margin-inline: auto; }
.assistant-status { color: var(--muted); }
.assistant-chat { min-height: 220px; max-height: 55vh; overflow-y: auto; display: grid; gap: 1rem; align-content: start; }
.assistant-empty { margin: auto; color: var(--muted); text-align: center; padding: 3rem 1rem; }
.assistant-message { padding: 1rem; border-radius: 10px; background: var(--surface-2); overflow-wrap: anywhere; }
.assistant-message.user { background: var(--water); margin-left: 10%; }
.user-text { white-space: pre-wrap; }
.assistant-suggestions { display: flex; flex-wrap: wrap; gap: .5rem; }
.assistant-suggestions button { text-align: left; font-size: .85rem; }
.assistant-form { display: grid; gap: .75rem; }
.assistant-form textarea { width: 100%; resize: vertical; padding: .75rem; color: var(--ink); background: var(--surface); border: 1px solid var(--field); border-radius: 6px; font: inherit; }
.assistant-actions { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: .75rem; }
.assistant-actions small { color: var(--muted); }
.assistant-markdown :deep(pre) { overflow-x: auto; white-space: pre-wrap; }
.assistant-markdown :deep(table) { display: block; overflow-x: auto; }
</style>
