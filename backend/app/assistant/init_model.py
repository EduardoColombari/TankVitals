"""Init idempotente: baixa somente se ausente, aquece e mantém em memória."""
import json
import os
import time
from urllib.request import Request, urlopen
from urllib.error import URLError


def main():
    base = os.getenv('OLLAMA_URL', 'http://ollama:11434').rstrip('/')
    model = os.getenv('OLLAMA_MODEL', 'llama3.2:3b')

    def call(path, data=None, timeout=1200):
        payload = None if data is None else json.dumps(data).encode()
        with urlopen(Request(base + path, data=payload, headers={'Content-Type': 'application/json'}), timeout=timeout) as response:
            return json.load(response)

    for attempt in range(60):
        try:
            models = call('/api/tags', timeout=5)['models']
            break
        except (URLError, TimeoutError):
            if attempt == 59:
                raise
            time.sleep(2)
    if not any(m.get('name') == model for m in models):
        print('Baixando', model, flush=True)
        result = call('/api/pull', {'model': model, 'stream': False})
        if result.get('error'):
            raise RuntimeError(result['error'])
    result = call('/api/chat', {'model': model, 'messages': [{'role': 'user', 'content': 'Responda: pronto.'}],
                              'stream': False, 'keep_alive': -1, 'options': {'num_ctx': 8192, 'num_predict': 8}})
    if not result.get('done') or result.get('error'):
        raise RuntimeError('Falha ao aquecer modelo')
    print('Modelo pronto:', model, flush=True)


if __name__ == '__main__':
    main()
