"""Serviço `rules`: o aerador automático e os alertas de faixa (IOT-07).

Consome `q.rules`, lê as regras e as faixas do PostgreSQL (recarregando de
tempos em tempos) e publica comandos em `tv.commands` e alertas em `tv.events`.
Roda com `python -m app.rules`.
"""
