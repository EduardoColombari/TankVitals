# TankVitals — Frontend

Dashboard em Vue 3 + TypeScript + Vite para o projeto de monitoramento IoT de tanque de aquicultura.

## Requisitos

- Node.js
- Backend TankVitals rodando em `http://localhost:8000`

## Instalação

```bash
npm install
```

## Desenvolvimento

Copie `.env.example` para `.env`.

Deixe `VITE_API_BASE_URL=` vazio para usar o proxy do Vite:

```bash
npm run dev
```

Acesse `http://localhost:5173`.

## Build

```bash
npm run build
npm run preview
```

## Integração

O frontend não conversa diretamente com MQTT ou InfluxDB. Ele consome somente a API FastAPI:

- `GET /api/health`
- `GET /api/thresholds`
- `GET /api/tanks`
- `GET /api/readings/latest`
- `GET /api/readings/history`
- `GET /api/stats`
- `WS /ws/live`

O WebSocket é usado como canal principal. Após falhas consecutivas, o dashboard passa automaticamente para polling de `/api/readings/latest` a cada 5 segundos.

## Observação sobre Chart.js

O gráfico utiliza duas linhas tracejadas para representar os limites mínimo/máximo da faixa segura, evitando adicionar dependências extras ao projeto.
