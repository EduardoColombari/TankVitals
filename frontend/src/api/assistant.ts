import { request, requestResponse } from './http'

export interface AssistantStatus { ready: boolean; available: boolean; model: string }
export const assistantStatus = (signal?: AbortSignal) => request<AssistantStatus>('/api/assistant/status', { signal })

export async function askAssistant(question: string, onToken: (token: string) => void, signal: AbortSignal) {
  const response = await requestResponse('/api/assistant', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body: JSON.stringify({ question }),
  })
  if (!response.body) throw new Error('Streaming não disponível neste navegador.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false
  function consume(line: string) {
    if (!line.trim()) return
    const event = JSON.parse(line) as { token?: string; done?: boolean; error?: string }
    if (event.error) throw new Error(event.error)
    if (typeof event.token === 'string') onToken(event.token)
    if (event.done) finished = true
  }
  try {
    while (!finished) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) consume(line)
      if (done) {
        consume(buffer)
        if (!finished) throw new Error('A conexão terminou antes de completar a resposta.')
        break
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
