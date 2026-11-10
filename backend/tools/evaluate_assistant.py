"""Executa as oito perguntas; saída deve ser revisada pela rubrica, sem falso 'aprovado'."""
import argparse
import json
from pathlib import Path
import httpx
from app.assistant.context import build_messages


class Fixture:
    def readings(self, measurement):
        if measurement == 'telemetry':
            return [{'device': 'od-01', 'values': {'oxygen_mg_l': 6.8}, 'timestamps': {'oxygen_mg_l': '2026-11-09T12:00:00Z'}}]
        return [{'device': 'bomba-01', 'values': {'health': 32, 'rul_h': 12}},
                {'device': 'bomba-02', 'values': {'health': 95, 'rul_h': 168}}]

    def alerts(self):
        return [{'device_id': 'bomba-01', 'level': 'critico', 'message': 'Saúde baixa', 'closed_at': None}]

    def training(self):
        return {'version': 'v3', 'metrics': {'health': {'mae': 2.1}, 'rul_h': {'mae': 4.2}}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:11434')
    parser.add_argument('--model', default='llama3.2:3b')
    args = parser.parse_args()
    cases = json.loads((Path(__file__).resolve().parents[1] / 'app/assistant/questions.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url=args.url, timeout=300) as client:
        for case in cases:
            response = client.post('/api/chat', json={'model': args.model, 'messages': build_messages(Fixture(), case['question']),
                'stream': False, 'keep_alive': -1, 'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 600}})
            response.raise_for_status()
            print(json.dumps({**case, 'model': args.model, 'answer': response.json()['message']['content']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
