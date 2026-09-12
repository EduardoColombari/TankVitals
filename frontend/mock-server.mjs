/**
 * Mock da API do TankVitals — fixture de desenvolvimento do front.
 *
 * NÃO é o backend. Serve para olhar o dashboard com dado plausível enquanto as
 * tarefas BE-01..09 não existem; quando o backend real subir na 8000, é só
 * parar este processo. Responde aos formatos da ARQUITETURA §6.
 *
 *   node mock-server.mjs                  # valores passeando dentro da faixa
 *   node mock-server.mjs --anomalia ph    # joga uma grandeza para fora
 *
 * Sem dependências de propósito: só o que vem no Node.
 */

import { createServer } from 'node:http'
import { createHash } from 'node:crypto'

const PORT = 8000
const TANK_ID = 'tanque-01'
const DEVICE_ID = 'esp32-tank-01'

const METRICS = ['temperature_c', 'ph', 'level_pct', 'turbidity_ntu']
const UNITS = {
  temperature_c: '°C',
  ph: 'pH',
  level_pct: '%',
  turbidity_ntu: 'NTU',
}

// Centro e amplitude do passeio. A temperatura cruza os 28 °C de vez em quando
// de propósito, para o card mudar de cor sozinho e dar para ver o "atencao".
const BASE = { temperature_c: 26.6, ph: 7.2, level_pct: 72, turbidity_ntu: 18 }
const AMPL = { temperature_c: 1.9, ph: 0.45, level_pct: 9, turbidity_ntu: 12 }
const ANOMALIA = { temperature_c: 31.4, ph: 5.5, level_pct: 11, turbidity_ntu: 71 }

const args = process.argv.slice(2)
const anomaliaIdx = args.indexOf('--anomalia')
const anomalia = anomaliaIdx >= 0 ? args[anomaliaIdx + 1] : null
if (anomalia && !METRICS.includes(anomalia)) {
  console.error(`--anomalia precisa ser uma de: ${METRICS.join(', ')}`)
  process.exit(1)
}

// --- regra de alerta (ARQUITETURA §5) ---------------------------------------

function classify(metric, v) {
  if (metric === 'temperature_c') {
    if (v < 22) return 'critico'
    if (v < 24) return 'atencao'
    if (v > 30) return 'critico'
    if (v > 28) return 'atencao'
    return 'ok'
  }
  if (metric === 'ph') {
    if (v < 6) return 'critico'
    if (v < 6.5) return 'atencao'
    if (v > 8.5) return 'critico'
    if (v > 8) return 'atencao'
    return 'ok'
  }
  if (metric === 'level_pct') {
    if (v < 15) return 'critico'
    if (v < 30) return 'atencao'
    return 'ok'
  }
  if (v > 60) return 'critico'
  if (v >= 40) return 'atencao'
  return 'ok'
}

const PIOR = { ok: 0, atencao: 1, critico: 2 }
const worst = (niveis) =>
  niveis.reduce((a, b) => (PIOR[b] > PIOR[a] ? b : a), 'ok')

// --- geração dos valores -----------------------------------------------------

// Determinístico no tempo: o histórico e a leitura atual contam a mesma
// história, então o último ponto do gráfico bate com o card.
function valorEm(metric, epochSec) {
  if (anomalia === metric) return round(ANOMALIA[metric], 2)
  const fase = epochSec / 900
  const i = METRICS.indexOf(metric)
  const onda =
    Math.sin(fase + i) * 0.7 + Math.sin(fase * 2.7 + i * 1.3) * 0.3
  return round(BASE[metric] + AMPL[metric] * onda, 2)
}

const round = (v, casas) => Math.round(v * 10 ** casas) / 10 ** casas
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z')

function leituraAtual() {
  const agora = Date.now()
  const valores = {}
  const niveis = {}
  for (const m of METRICS) {
    valores[m] = valorEm(m, Math.floor(agora / 1000))
    niveis[m] = classify(m, valores[m])
  }
  return {
    tank_id: TANK_ID,
    device_id: DEVICE_ID,
    time: iso(agora),
    age_s: round(Math.random() * 2, 1),
    online: true,
    level: worst(Object.values(niveis)),
    metrics: Object.fromEntries(
      METRICS.map((m) => [
        m,
        { value: valores[m], level: niveis[m], unit: UNITS[m] },
      ]),
    ),
  }
}

const RANGE_SEG = { '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800 }
const JANELA = { '1h': '10s', '6h': '1m', '24h': '5m', '7d': '1h' }
const JANELA_SEG = { '10s': 10, '1m': 60, '5m': 300, '1h': 3600 }

function historico(range, metrics) {
  const janela = JANELA[range]
  const passo = JANELA_SEG[janela]
  const fim = Math.floor(Date.now() / 1000)
  const inicio = fim - RANGE_SEG[range]

  return {
    tank_id: TANK_ID,
    range,
    window: janela,
    series: metrics.map((m) => {
      const points = []
      for (let t = inicio; t <= fim; t += passo) {
        points.push({ t: iso(t * 1000), v: valorEm(m, t) })
      }
      return { metric: m, unit: UNITS[m], points }
    }),
  }
}

function estatisticas(range) {
  const h = historico(range, METRICS)
  const metrics = {}
  for (const s of h.series) {
    const vs = s.points.map((p) => p.v)
    metrics[s.metric] = {
      min: round(Math.min(...vs), 2),
      max: round(Math.max(...vs), 2),
      avg: round(vs.reduce((a, b) => a + b, 0) / vs.length, 2),
      last: vs[vs.length - 1],
    }
  }
  return { tank_id: TANK_ID, range, count: h.series[0].points.length, metrics }
}

const THRESHOLDS = {
  temperature_c: { criticalBelow: 22, warningBelow: 24, safeMin: 24, safeMax: 28, warningAbove: 28, criticalAbove: 30, unit: '°C', label: 'Temperatura' },
  ph: { criticalBelow: 6, warningBelow: 6.5, safeMin: 6.5, safeMax: 8, warningAbove: 8, criticalAbove: 8.5, unit: 'pH', label: 'pH' },
  level_pct: { criticalBelow: 15, warningBelow: 30, safeMin: 30, safeMax: 100, unit: '%', label: 'Nível' },
  turbidity_ntu: { safeMin: 0, safeMax: 40, warningAbove: 40, criticalAbove: 60, unit: 'NTU', label: 'Turbidez' },
}

// --- HTTP --------------------------------------------------------------------

function json(res, body, status = 200) {
  const texto = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Content-Length': Buffer.byteLength(texto),
  })
  res.end(texto)
}

const servidor = createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`)
  const p = url.pathname
  const range = url.searchParams.get('range') ?? '6h'

  if (p === '/api/health') {
    return json(res, { status: 'ok', mqtt: 'connected', influxdb: 'ok', mock: true })
  }
  if (p === '/api/thresholds') return json(res, THRESHOLDS)
  if (p === '/api/tanks') {
    return json(res, [
      { tank_id: TANK_ID, device_id: DEVICE_ID, last_seen: iso(Date.now()), online: true },
    ])
  }
  if (p === '/api/readings/latest') return json(res, leituraAtual())
  if (p === '/api/readings/history') {
    if (!RANGE_SEG[range]) {
      return json(res, { detail: `range inválido: ${range}` }, 400)
    }
    const pedido = url.searchParams.get('metrics')
    const metrics = pedido
      ? pedido.split(',').filter((m) => METRICS.includes(m))
      : METRICS
    return json(res, historico(range, metrics.length ? metrics : METRICS))
  }
  if (p === '/api/stats') {
    if (!RANGE_SEG[range]) {
      return json(res, { detail: `range inválido: ${range}` }, 400)
    }
    return json(res, estatisticas(range))
  }

  json(res, { detail: 'rota inexistente no mock' }, 404)
})

// --- WebSocket ---------------------------------------------------------------
// Handshake e enquadramento na mão para não precisar do pacote `ws`: o mock só
// envia texto e só precisa entender o frame de close que o navegador manda.

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
const clientes = new Set()

servidor.on('upgrade', (req, socket) => {
  if (!new URL(req.url, 'http://x').pathname.startsWith('/ws/live')) {
    socket.destroy()
    return
  }

  const aceite = createHash('sha1')
    .update(req.headers['sec-websocket-key'] + GUID)
    .digest('base64')

  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
      'Upgrade: websocket\r\n' +
      'Connection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${aceite}\r\n\r\n`,
  )

  clientes.add(socket)
  enviar(socket, { type: 'reading', payload: leituraAtual() })

  socket.on('data', (buf) => {
    // opcode 0x8 = close
    if ((buf[0] & 0x0f) === 0x8) socket.end()
  })
  const sair = () => clientes.delete(socket)
  socket.on('close', sair)
  socket.on('error', sair)
})

function enviar(socket, objeto) {
  const dados = Buffer.from(JSON.stringify(objeto))
  let cabecalho
  if (dados.length < 126) {
    cabecalho = Buffer.from([0x81, dados.length])
  } else if (dados.length < 65536) {
    cabecalho = Buffer.alloc(4)
    cabecalho[0] = 0x81
    cabecalho[1] = 126
    cabecalho.writeUInt16BE(dados.length, 2)
  } else {
    cabecalho = Buffer.alloc(10)
    cabecalho[0] = 0x81
    cabecalho[1] = 127
    cabecalho.writeBigUInt64BE(BigInt(dados.length), 2)
  }
  socket.write(Buffer.concat([cabecalho, dados]))
}

// Mesmo ritmo do ESP32 (ARQUITETURA §1): uma leitura a cada 5 s.
setInterval(() => {
  if (!clientes.size) return
  const msg = { type: 'reading', payload: leituraAtual() }
  for (const socket of clientes) {
    try {
      enviar(socket, msg)
    } catch {
      clientes.delete(socket)
    }
  }
}, 5000)

servidor.listen(PORT, () => {
  console.log(`mock da API em http://localhost:${PORT}  (WebSocket em /ws/live)`)
  console.log(anomalia ? `anomalia forcada em ${anomalia}` : 'valores dentro da faixa')
  console.log('NAO e o backend real - pare este processo quando as BE-01..09 subirem')
})
