import assert from 'node:assert/strict'
import { renderAssistantMarkdown as render } from '../src/assistantMarkdown.ts'

for (const input of [
  '<script>alert(1)</script>',
  '<img src=x onerror=alert(1)>',
  '![x](https://example.com/a.png)',
  '[x](javascript:alert(1))',
  '[x](data:text/html;base64,PHNjcmlwdD4=)',
]) {
  const html = render(input)
  assert(!/<script|<img|href="(?:javascript|data):/i.test(html), html)
}
assert(render('<script>alert(1)</script>').includes('&lt;script&gt;'))
assert(render('**Seguro**').includes('<strong>Seguro</strong>'))
console.log('Markdown seguro: HTML escapado, imagens e URLs executáveis bloqueados.')
