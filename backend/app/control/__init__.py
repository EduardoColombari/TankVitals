"""Rota de comando: `POST /api/devices/{id}/command` (IOT-04).

O começo do fluxo Web → IoT: o botão chama a API, e a API publica o comando no
barramento. Como a API (login, papéis, banco) é da BACK, este pacote não
cria a aplicação nem o login: entrega um `APIRouter` pronto para a API incluir,
e recebe de fora a dependência de autorização. Veja o exemplo em `router.py`.
"""
