# Migrar os cadastros da skill PowerShell

Este documento explica a migração **já implementada e testada nas etapas 3–4**. A etapa 8 não executou migração na instalação operacional. O comando copia metadados para um diretório novo; não move sites, não baixa arquivos, não importa FileZilla e não extrai senhas DPAPI.

## O que é preservado e o que muda

| Origem | Destino/comportamento |
|---|---|
| `config/sites.json` | `sites.yaml`, com domínio completo e campos validados |
| `username`, `localRoot`, `remoteRoot` | `user`, `local_root`, `remote_root` |
| Fingerprint SHA256 suportada | Preservada, removendo apenas prefixo WinSCP de tipo/tamanho; não é conferida na rede |
| Caminhos Windows/Linux/macOS | Preservados; mudança de SO requer mapeamento explícito |
| Publicação anteriormente habilitada | Desativada no site e na configuração global |
| `config/settings.json` | Validado; novo `settings.yaml` recebe padrões Python, sem importar habilitação de envio |
| Senhas `.dpapi` | Não lidas; todos os domínios ficam pendentes de recadastro no provedor escolhido |
| Logs, backups, sites e scripts antigos | Permanecem onde estavam; não são copiados nem excluídos |

FTP em claro, fingerprints MD5/wildcards/listas e campos não suportados são recusados. Não habilite aceitação automática de chave para contornar um erro. Corrija conscientemente uma **cópia** dos metadados, ou cadastre o domínio pelo assistente Python.

## Pré-requisitos

Instale a CLI conforme [instalação](install.md). A origem deve ser a raiz da skill contendo os dois arquivos `config/sites.json` e `config/settings.json`. Use diretórios reais: links, reparse points/junções e UNC são recusados. A pasta de destino não pode existir, nem estar dentro da origem. Seu diretório pai precisa existir e deve ter acesso restrito.

Não escolha a pasta `public_html` como origem/destino da configuração. Mantenha configurações, credenciais e estado fora da pasta publicável. Se trocar de máquina/SO, transfira os dois JSONs de metadados por meio privado; eles contêm informações da sua infraestrutura, mesmo sem senha.

## Executar a conversão

Exemplo PowerShell com diretório pai existente, escolhendo um nome novo:

```powershell
mcp-locaweb-sftp migrate --from "$env:USERPROFILE\.codex\skills\mcplocaweb" --to "$env:USERPROFILE\locaweb-python-migrado"
```

Exemplo POSIX, usando uma cópia privada dos metadados da origem:

```sh
mcp-locaweb-sftp migrate --from "$HOME/skill-antiga" --to "$HOME/locaweb-python-migrado" --local-root "exemplo.com.br=$HOME/sites/exemplo.com.br/public_html"
```

`--from` **e** `--to` são obrigatórios. Repita `--local-root "dominio=caminho-absoluto"` para cada domínio cujo caminho mudou. O domínio precisa existir na origem; duplicatas e nomes desconhecidos são recusados. Não há conversão automática de `C:/...` para `/home/...`. Se não mudar de SO/caminho, omita esse argumento.

Em sucesso, são criados `sites.yaml`, `settings.yaml` e, por último, `migration.json`. A CLI retorna `status: success`, mas isso significa **metadados convertidos**, não sites prontos para publicar. O relatório contém `pending_credentials` e `pending_ssh_fingerprints`; é um retrato da conversão, não um monitor que se atualiza depois.

## Completar a preparação sem mover a instalação antiga

Use explicitamente o destino novo em **todas** as chamadas seguintes. Exemplo PowerShell:

```powershell
$cadastroMigrado = "$env:USERPROFILE\locaweb-python-migrado"
$opcoesLocaweb = @('--sites', "$cadastroMigrado\sites.yaml", '--settings', "$cadastroMigrado\settings.yaml", '--state-dir', "$cadastroMigrado\state")
mcp-locaweb-sftp @opcoesLocaweb list
mcp-locaweb-sftp @opcoesLocaweb info exemplo.com.br
```

1. Confira domínio, host, porta, usuário, pasta local e raiz remota. Uma pasta duplicada `public_html/public_html` não é corrigida automaticamente.
2. Confirme a fingerprint SFTP com o provedor. Se houver pendência, use `scan-key`, confira independentemente e só então `set-key ... --confirm`, sempre com as opções de caminho acima. Para FTPS, confira certificado/CA; não existe modo TLS inseguro.
3. Escolha `credential_store` por site. A migração usa o padrão keyring; age/ambiente devem ser configurados explicitamente. Mudanças de identidade da conexão alteram o vínculo da credencial: termine essa revisão antes do recadastro.
4. Execute `mcp-locaweb-sftp @opcoesLocaweb credential exemplo.com.br`. A senha é digitada no terminal oculto; não a exporte do DPAPI para texto.
5. Quando autorizar a conexão, execute `test` e `compare` com as mesmas opções e o domínio. Ambos acessam o servidor, mas não publicam arquivos.
6. Aponte o cliente MCP para os mesmos arquivos e a mesma pasta de estado. Publicação continua desativada até o opt-in explícito descrito na [CLI](cli.md#publicar-uma-prévia-revisada).

No POSIX, passe `--sites`, `--settings` e `--state-dir` antes do subcomando, com os caminhos equivalentes. Não use o array PowerShell em um shell POSIX.

## Falhas e retorno ao legado

Destino existente é recusado. Falha durante gravação conserva a saída incompleta para inspeção; a origem permanece intacta. Não apague o destino automaticamente para tentar de novo: examine-o e escolha outro diretório novo. A gravação exclusiva depende de hard links do sistema de arquivos; se não houver suporte, falha sem substituir a origem.

O [fluxo PowerShell](legacy-windows.md) continua disponível. Voltar a usá-lo é uma escolha operacional, **não** um rollback dos arquivos remotos. Não execute publicações pelo legado e pelo Python ao mesmo tempo: as travas deles não se coordenam. Não copie senhas, tokens, bancos SQLite, diários ou backups para o Git.

O migrador não é um comando único que torne a hospedagem pronta: a conversão de metadados é um comando; conferência de identidade, recadastro de senha e habilitação de publicação são passos separados. A migração de produção e a validação de cofres reais continuam fora da evidência automatizada do projeto.
