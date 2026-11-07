# Validação isolada da PLAT-06

Execute no PowerShell do desenvolvedor, a partir de qualquer diretório:

```powershell
./scripts/tests/test_writer_influx_integration.ps1
```

O runner exige acesso ao Docker Engine e Docker Compose. Ele gera credenciais em
uma pasta temporária única, cria um volume RabbitMQ com definições geradas,
sobe uma instância descartável de RabbitMQ e InfluxDB, executa os cenários e
remove somente o projeto, as imagens marcadas para aquela execução e o volume
de definições que criou. Um diretório de diagnóstico separado permanece em
`%TEMP%` com logs do Compose; os valores do `.env` temporário são redigidos
antes de gravar os logs. A pasta temporária com credenciais é removida.

Os cenários publicados pelo cliente incluem token de escrita restrito ao
bucket, gravação e ACK, falha temporária seguida de retry, três falhas seguidas
de DLQ, comportamento de ponto duplicado e recuperação de mensagem após
reiniciar o Writer. O proxy interno injeta respostas HTTP 400 controladas uma
vez ou três vezes: esse erro não aciona o retry interno de transporte do SDK e
permite verificar o retry da aplicação sem atrasos longos. O proxy não publica
portas no host.

O resultado só deve ser considerado aprovado depois de executar o runner no
host com Docker. A validação de builds é separada:

```powershell
./scripts/tests/validate_plat_images.ps1
```

Esse segundo script verifica os contratos estáticos, tenta construir cada
imagem com tags temporárias e executa `nginx -t` na imagem frontend quando o
build termina. Ele remove apenas as imagens com o sufixo único que criou.

Para instalar dependências frontend e validar TypeScript/build no host:

```powershell
./scripts/tests/validate_frontend.ps1 -AllowNpmCiToReplaceNodeModules
```

Use o parâmetro somente se aceita que `npm ci` substitua `frontend/node_modules`.
O script não altera o lockfile e informa os valores de proxy apenas como
definido/indefinido, sem exibi-los.

`scripts/tests/test_dependency_conflicts.py` verifica pins exatos locais e os
arquivos correspondentes em `origin/main`. Se Git não puder ler a referência
remota, ele reporta a verificação remota como incompleta. Não consolida versões
de dependências de outras frentes.
