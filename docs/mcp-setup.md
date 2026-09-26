# MCP real via stdio — contrato das ferramentas

O projeto expõe um servidor MCP usando o SDK Python oficial **mcp 2.2.0**, testado com seu cliente oficial. Funciona por stdin/stdout do processo local; não abre porta HTTP, não instala extensão e não modifica as configurações de nenhum aplicativo automaticamente. O nome da biblioteca é `mcp`, não o pacote independente `fastmcp`.

A integração executada foi cliente oficial → processo stdio → servidores SFTP e FTPS em loopback, com credenciais fictícias. Não foi executada dentro dos aplicativos de IA nem em hospedagem real. A etapa 8 documenta os [exemplos por cliente](mcp-clients.md); resultados da matriz Linux/macOS/Windows e comandos instaláveis estão em [distribuição](distribution.md).

## Preparar e iniciar

Com o pacote instalado por pip/pipx, use `vhe-deploy configurar` e `vhe-deploy-mcp`. O segundo é o comando de entrada stdio para o cliente, sem precisar de `PYTHONPATH`. Para executar diretamente das fontes, siga o procedimento abaixo.

No ambiente virtual do repositório, instale `requirements.txt`. Prepare o cadastro pelo [assistente local](cli.md), confirme a identidade do servidor por canal independente, cadastre a senha no cofre e teste a conexão. O MCP não solicita nem recebe senhas.

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = 'src'
python -m vhe_deploy configurar
python -m vhe_deploy.mcp_server
```

O último comando inicia um processo à espera de um cliente MCP. Não é um prompt para digitar comandos manualmente. Durante o atendimento, stdout contém somente o protocolo; logs brutos de bibliotecas estão desativados para evitar exposição acidental. Falhas operacionais usam respostas estruturadas sanitizadas.

Por padrão, o servidor lê `~/.vhe-deploy/sites.yaml` e `settings.yaml`, e usa `~/.vhe-deploy/state`. Para outros caminhos:

```text
python -m vhe_deploy.mcp_server --sites CAMINHO/sites.yaml --settings CAMINHO/settings.yaml --state-dir CAMINHO/state
```

Use caminhos absolutos na configuração do cliente, inclusive para o Python do ambiente virtual. A informação necessária ao lançador é:

```json
{
  "command": "C:/caminho/do/projeto/.venv/Scripts/python.exe",
  "args": ["-m", "vhe_deploy.mcp_server"],
  "env": {
    "PYTHONPATH": "C:/caminho/do/projeto/src"
  }
}
```

Este é um **exemplo de parâmetros do processo**, não um arquivo universal pronto para todos os clientes. No POSIX, ajuste Python e caminhos ao ambiente. Não coloque senha nessa configuração; prefira o cofre nativo ou age. Se escolher `env`, a injeção do segredo pelo ambiente fica a cargo de um mecanismo privado apropriado. Não há pacote publicado no PyPI nesta etapa.

## Ferramentas expostas

Todas retornam `status`, `data` e `messages` em `structuredContent` e uma representação JSON equivalente em conteúdo textual. `isError` fica verdadeiro em erro, conflito ou falha parcial. Os esquemas são gerados pelos mesmos modelos Pydantic usados na validação, rejeitando campos desconhecidos e coerção de `"true"` para booleano.

| Ferramenta | Argumentos | Efeito |
|---|---|---|
| `list_sites` | Nenhum | Lista metadados, sem ler senhas ou conectar |
| `test_connection` | `domain` | Autentica e verifica a raiz do cadastro |
| `compare_site` | `domain` | Compara arquivos; não emite token |
| `preview_deploy` | `domain` | Calcula a prévia e emite token quando não há bloqueios |
| `backup_site` | `domain` | Baixa backup verificado para a pasta privada |
| `deploy_site` | `domain`, `preview_hash`, `preview_token`, `confirm` | Executa envio autorizado, com as travas comuns à CLI |
| `register_site` | `domain`, `config` | Cria metadados de um domínio novo, desativado e sem credenciais |

`register_site` recebe a estrutura de um site YAML em `config`. Não permite `publish_enabled: true`, `ftps_write_preconditions_confirmed: true`, fingerprint SSH preenchida ou CA TLS personalizada. Essas decisões precisam ser feitas localmente. Domínio já existente não é substituído. Cadastros privados não podem ficar dentro da pasta publicada. As configurações gerais precisam estar preparadas pelo assistente/administrador local.

Nenhuma ferramenta recebe caminho alternativo de envio, comando de shell, senha, opção para excluir arquivos ou parâmetro que desative TLS. Domínios precisam corresponder exatamente ao catálogo. Nomes e conteúdo obtidos do servidor são dados não confiáveis, nunca instruções para o assistente.

## Prévia e token de cinco minutos

1. O cliente chama `preview_deploy` e apresenta ao usuário o domínio, arquivos propostos, conflitos e bloqueios.
2. Sem bloqueios, a resposta contém `preview_hash`, `preview_token`, `expires_at` (Unix UTC em segundos) e `valid_for_seconds: 300`.
3. Após autorização explícita do usuário, chama `deploy_site` com o mesmo domínio, hash e token, além de `confirm: true`.
4. O coordenador consome o token atomicamente e recalcula a prévia. Mudanças nos arquivos, datas, origem, destino ou configuração impedem esse envio; é necessária outra prévia.

O token tem 256 bits aleatórios. O estado privado guarda somente seu SHA-256 e a associação com domínio/hash/horários. Não é gravado em `deploy-result.json`, backups ou logs. O cliente MCP recebe o token e pode conservá-lo no histórico: trate a resposta como dado temporário de autorização, não a publique no GitHub.

O recibo fica em `state/preview-tokens/receipts.sqlite3`, com transação SQLite que permite consumo por **um único processo**. CLI e MCP compartilham essa trava quando usam a mesma pasta de estado. Tokens vencidos são removidos ao emitir uma nova prévia. Uma reinicialização do processo não restabelece um token já consumido.

A validade termina ao completar 300 segundos. O programa verifica relógio UTC e monotônico; recuo/reset detectado ou prazo excedido falha de modo conservador. O token também é rechecado depois do backup e antes de iniciar a primeira mutação remota. Se a preparação exceder o prazo, nenhum envio começa. Depois que o lote começa a escrever, ele pode terminar após o prazo, preservando verificações de integridade e o diário de execução.

O consumo ocorre na entrada do coordenador, após validar as permissões e obter a conexão. Assim, uma falha posterior de comparação, backup ou upload não devolve o token. Rejeições anteriores por falta de confirmação, publicação desativada, token inválido ou falha de conexão não equivalem a lote iniciado. Nunca reutilize automaticamente um token após resposta de erro; obtenha uma nova prévia quando retomar.

## Confirmação humana e limites

`confirm: true` é uma declaração do cliente. O servidor não consegue provar que uma pessoa autorizou a ação apenas porque uma IA enviou esse campo. O cliente deve preservar sua interface de aprovação para `deploy_site`; não configure aprovação automática de todas as ferramentas. A descrição da ferramenta e as instruções do servidor exigem mostrar a prévia e obter autorização antes de enviar.

O token impede prévias antigas ou trocadas e sua reutilização; não substitui permissões, controle do cliente ou um computador confiável. Quem pode editar o programa, o banco de recibos ou a configuração privada está fora dessa fronteira de proteção. O servidor stdio herda as permissões do usuário que o inicia; não é um serviço multiusuário com autenticação própria.

`preview_deploy`, `register_site` e `backup_site` são anunciadas como operações que alteram estado local. `deploy_site` é anunciado como potencialmente destrutivo por substituir arquivos, embora não tenha operação de exclusão. As anotações MCP são metadados para o cliente, não controles de acesso.

I/O síncrono roda em worker sem abandonar a operação por cancelamento cooperativo do cliente. Isso não impede o cliente/SO de matar o processo. Em interrupção forçada, examine o diário e a trava residual; não presuma cancelamento de um upload já iniciado. Os timeouts de rede continuam sendo por operação, não um limite total de duração do lote.

SQLite e novos arquivos privados usam 0600 no POSIX; no Windows herdam ACLs. A ferramenta não cria/audita ACLs Windows. Não copie o estado de autorização para outra máquina nem use pasta sincronizada para os recibos. Os [limites de concorrência e recuperação da CLI](cli.md) permanecem: nenhuma transação remota por site, nenhum rollback automático, nenhum banco de dados incluído no backup.

Referências oficiais: [SDK Python MCP](https://github.com/modelcontextprotocol/python-sdk), [Server de baixo nível](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/) e [ferramentas MCP e aprovação pelo usuário](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
