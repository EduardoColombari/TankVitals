"""Código compartilhado entre os serviços do 2º bimestre (BACK-01).

Este pacote nasceu com o que a frente IOT precisa: o envelope das mensagens, os
tópicos e as routing keys, a classificação por faixa, a conexão com o RabbitMQ
e o acesso ao PostgreSQL. A BACK e a IA acrescentam aqui o resto (`physics.py`,
`features.py`...), sem precisar mexer nestes arquivos.

Regra de organização: os módulos de lógica (`envelope`, `topics`, `ranges`)
só usam a biblioteca padrão e rodam em qualquer lugar; quem importa
`aio-pika`, `sqlalchemy` ou `pydantic` fica em módulos separados (`rabbit`,
`pg`, `schema`).
"""
