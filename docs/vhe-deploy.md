# VHE Deploy

O projeto Python passa a usar a marca **VHE Deploy**, porque opera servidores SFTP/FTPS compatíveis de diferentes provedores. A mudança de nome não acrescenta protocolos, administração de DNS/e-mail ou suporte automático a todas as plataformas.

| Elemento | Nome atual |
|---|---|
| Pacote Python | `vhe-deploy` |
| CLI | `vhe-deploy` |
| Servidor MCP stdio | `vhe-deploy-mcp` |
| Módulo das fontes | `vhe_deploy` |
| Nome anunciado no protocolo MCP | `vhe-deploy` |
| Identificador nos exemplos de clientes | `vheDeploy` |
| Diretório privado padrão | `~/.vhe-deploy` |

```sh
vhe-deploy list
vhe-deploy info exemplo.com.br
vhe-deploy test exemplo.com.br
vhe-deploy compare exemplo.com.br
vhe-deploy backup exemplo.com.br
vhe-deploy preview exemplo.com.br
```

Os comandos antigos **não são aliases**. A alteração ocorre durante desenvolvimento, antes da distribuição a terceiros. Wheel, Docker e binários usam os novos comandos; os exemplos de seis clientes MCP também foram atualizados. O pacote não foi publicado no PyPI: instale por fonte ou wheel conforme [instalação](install.md).

Não há importação silenciosa de dados antigos. As opções `--sites`, `--settings` e `--state-dir` continuam disponíveis para indicar uma instalação explicitamente. O namespace do cofre agora é `vhe-deploy/v1` e a variável por conexão usa o prefixo `VHE_DEPLOY_PASSWORD_`; não se deve assumir que senhas de protótipos anteriores serão encontradas automaticamente. Se houver um protótipo anterior fora do ambiente de teste, preserve seus dados e recadastre a credencial pelo terminal local antes de operar.

A retenção de [três envios concluídos](retention.md), os tokens de prévia, os sete nomes de ferramentas MCP e as verificações de identidade continuam em vigor. Os registros históricos das oito etapas e os scripts PowerShell legados preservam seus nomes originais para distinguir o que foi testado em cada época.

## Verificação da mudança

Em Windows/Python 3.14.3, a suíte com o módulo renomeado passou com **493 testes e um skip** de criação de symlink sem privilégio, cobertura de instruções/ramos **94,94%**. Após acrescentar proteção também à pasta privada antiga e conferir o nome anunciado no MCP, os 122 testes de guards/MCP passaram. Wheel e sdist foram conferidos; os metadados do wheel expõem exclusivamente os dois novos comandos.

A instalação isolada por pip/pipx passou, incluindo descoberta MCP e envio com os dois protocolos locais SFTP/FTPS. O cofre real Windows e a descoberta das sete ferramentas pelo Codex App Server foram novamente testados com o novo pacote e namespace, usando somente um cadastro fictício em loopback. A credencial temporária foi removida. Nenhuma hospedagem real ou configuração permanente do Codex foi alterada; a aprovação na interface desktop continua fora dessa evidência.
