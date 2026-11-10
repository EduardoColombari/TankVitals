import asyncio
import json
import logging
import math
import time
from collections import deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai_api import current_user
from app.config import settings
from .context import ContextRepository, build_messages

router = APIRouter(prefix='/api/assistant', tags=['Assistente'])
logger = logging.getLogger(__name__)


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=2000)

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Pergunta vazia')
        return value.strip()


class RateLimiter:
    """Janela móvel da API de processo único; sem await entre conferir e reservar."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.users = clock, {}

    def reserve(self, user):
        now = self.clock()
        for key in list(self.users):
            queue = self.users[key]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if not queue:
                del self.users[key]
        queue = self.users.setdefault(user, deque())
        if len(queue) >= 5:
            return max(1, math.ceil(60 - (now - queue[0])))
        queue.append(now)
        return 0


def repository(request: Request):
    return ContextRepository(request.app.state.repo,
                             getattr(request.app.state, 'assistant_db', None)
                             or getattr(request.app.state, 'database', None),
                             getattr(request.app.state, 'ai_store', None))


def client():
    return httpx.AsyncClient(base_url=settings.ollama_url, timeout=httpx.Timeout(180, connect=5))


@router.get('/status')
async def status(user=Depends(current_user)):
    try:
        async with client() as http:
            response = await http.get('/api/ps', timeout=5)
            response.raise_for_status()
            ready = any(m.get('name') == settings.ollama_model or m.get('model') == settings.ollama_model
                        for m in response.json()['models'])
        return {'ready': ready, 'available': True, 'model': settings.ollama_model}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {'ready': False, 'available': False, 'model': settings.ollama_model}


@router.post('')
async def ask(body: Question, request: Request, user=Depends(current_user), repo=Depends(repository)):
    if not hasattr(request.app.state, 'assistant_limiter'):
        request.app.state.assistant_limiter = RateLimiter()
    retry = request.app.state.assistant_limiter.reserve(user['sub'])
    try:
        await asyncio.to_thread(repo.audit, user['sub'], body.question, not bool(retry),
                                'rate_limit' if retry else None)
    except Exception:
        logger.exception('Falha na auditoria do assistente')
        raise HTTPException(503, 'Auditoria indisponível; pergunta não enviada ao modelo') from None
    if retry:
        raise HTTPException(429, 'Limite de 5 perguntas por minuto. Aguarde.', headers={'Retry-After': str(retry)})
    messages = await asyncio.to_thread(build_messages, repo, body.question)
    http = client()
    try:
        upstream = await http.send(http.build_request('POST', '/api/chat', json={
            'model': settings.ollama_model, 'messages': messages, 'stream': True,
            'keep_alive': -1, 'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 600},
        }), stream=True)
        upstream.raise_for_status()
    except (httpx.HTTPError, asyncio.CancelledError) as exc:
        await http.aclose()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(503, 'Ollama indisponível ou modelo não instalado') from None

    async def chunks():
        try:
            async for line in upstream.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get('error'):
                    raise ValueError('Ollama interrompeu a geração')
                content = event.get('message', {}).get('content', '')
                if content:
                    yield json.dumps({'token': content}, ensure_ascii=False) + '\n'
                if event.get('done'):
                    yield '{"done":true}\n'
                    return
            raise ValueError('Stream incompleto')
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            logger.exception('Stream do assistente interrompido')
            yield json.dumps({'error': 'Resposta interrompida. Tente novamente.'}) + '\n'
        finally:
            await upstream.aclose()
            await http.aclose()

    return StreamingResponse(chunks(), media_type='application/x-ndjson',
                             headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'})
