# Validação local: cofre Windows, backup seletivo e descoberta no Codex

Executada em **26/09/2026**, após as oito etapas, sobre o código entregue até `656824f`. Ambiente: Windows X64, Python 3.14.3, pacote instalado `0.1.0a1` e Codex CLI **0.158.0-alpha.2**. Este é um registro de verificação manual local, separado da matriz de CI.

## Cenário e resultados

Foi usada a fixture SFTP baseada em `paramiko.ServerInterface`, ouvindo somente em loopback, com porta e chave SSH temporárias. O cadastro fictício `codex-lab.example.com` apontou para esse servidor. Configuração, arquivos e estado ficaram em diretório temporário; o processo MCP instalado rodou fora do checkout, com `PYTHONPATH` vazio.

| Verificação | Resultado observado |
|---|---|
| Cofre real do Windows | `WinVaultKeyring` gravou e leu uma única credencial fictícia, vinculada ao cadastro temporário |
| MCP instalado + cofre real | O processo filho recuperou a credencial do cofre e autenticou no SFTP local |
| Publicação desativada | Envio bloqueado antes de nova autenticação no servidor |
| Atualização de `index.html` | Versão remota antiga preservada; conteúdo novo verificado no servidor |
| Inclusão de `new.txt` | Arquivo enviado sem criar backup de uma versão anterior inexistente |
| Arquivo remoto `keep.txt` | Preservado; não entrou no backup automático do envio |
| Escopo do backup automático | A pasta `backup/files/` continha somente `index.html` |
| Reutilização do token | Segunda tentativa rejeitada |
| Backup solicitado separadamente | Copiou os três arquivos do servidor de teste, com resultado de sucesso |
| Configuração temporária do Codex | Somente o MCP de teste habilitado; demais MCPs/plugins desativados por overrides do processo |
| Descoberta pelo Codex | `initialize` e `mcpServerStatus/list` do App Server reconheceram as sete ferramentas |
| Encerramento | Credencial fictícia removida e ausência conferida; SFTP e processos filhos encerrados; diretório temporário removido |

O teste consultou somente a chave de credencial criada para esse cadastro. Não enumerou credenciais, não usou FileZilla, não acessou hospedagens reais e não gravou configuração permanente do Codex. Nenhum segredo, token, log bruto ou cadastro operacional integra este registro público.

Também foram executados **oito testes existentes**, cobrindo SFTP e FTPS: backup antes da substituição, falha de backup impedindo uploads, backup adulterado bloqueando substituição e backup completo incluindo arquivo exclusivamente remoto.

```sh
python -m pytest -q tests/test_deploy.py -k 'backup_precedes or backup_failure_prevents or corrupted_backup or full_backup_includes'
```

## Interpretação e limites

- **Backup automático do deploy é seletivo:** salva os originais dos arquivos que serão substituídos. Os arquivos locais enviados também ficam em `sources/` para garantir o conteúdo aprovado. Não há cópia automática integral do site.
- **Na versão deste teste ainda não havia limpeza automática.** Uma alteração posterior acrescentou [retenção de três envios concluídos por domínio](retention.md), com testes próprios. Não atribua essa validação à execução histórica descrita aqui.
- A comparação calcula SHA-256 lendo o conteúdo remoto, inclusive de arquivos que acabarão sem alterações. Portanto, backup seletivo economiza armazenamento, mas não significa tráfego limitado aos arquivos enviados. Sites grandes continuam sujeitos ao custo de inventário/revalidações.
- O teste com credencial real no cofre foi do pacote instalado, não do binário PyInstaller. Linux/macOS e outras contas/máquinas Windows continuam sem validação de cofre real.
- No Codex foi testada **inicialização e descoberta via App Server**, sem turno de modelo e sem criar chat. As operações de arquivo foram chamadas pelo cliente oficial do SDK MCP. Isso não comprova o fluxo de aprovação na interface desktop nem interpretação de pedidos em linguagem natural.
- Os demais aplicativos MCP continuam com exemplos documentados, sem homologação de interface. A versão do Codex testada é alpha; o resultado não garante todas as versões.

O protocolo foi conferido na [documentação oficial do App Server](https://learn.chatgpt.com/docs/app-server) e nos schemas gerados pelo executável instalado. Consulte também [clientes MCP](mcp-clients.md), [CLI e limites de armazenamento/tráfego](cli.md) e [recuperação](recovery.md).
