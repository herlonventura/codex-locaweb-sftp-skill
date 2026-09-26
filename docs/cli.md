# CLI Python — cadastro, prévia, backup e envio

A CLI da etapa 4 funciona a partir do código-fonte. Não depende do FileZilla, WinSCP ou de dados já salvos no computador. A skill PowerShell existente mantém seu funcionamento anterior; ainda não foi trocada por esta CLI. O servidor MCP e o token efêmero da etapa 5 ainda não estão implementados.

## Executar a partir do repositório

Requer Python 3.11+ e as dependências de `requirements.txt`. A execução comprovada até esta etapa é Windows/Python 3.14.3. Em um ambiente virtual, instale as dependências e use:

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = 'src'
python -m mcp_locaweb_sftp --help
python -m mcp_locaweb_sftp configurar
```

Em um terminal POSIX, o equivalente para carregar o código-fonte é `export PYTHONPATH=src`. Isso descreve a chamada, não comprova execução nesse sistema. Pacote instalável, pipx, contêiner e binários pertencem à etapa 7.

## Primeira instalação: perguntas por site

`setup`, também chamado `configurar`, pede:

1. Domínio completo, que identificará o site nas operações futuras.
2. Protocolo: SFTP ou FTPS explícito. FTP sem criptografia não é aceito.
3. Servidor e porta. Sugere o domínio como servidor e 22/21 conforme o protocolo; confira os valores fornecidos pela hospedagem.
4. Usuário, pasta local absoluta e pasta remota. `/public_html` é uma sugestão, não descoberta automática: informe a raiz correta da conta.
5. Provedor de credenciais: `keyring`, `age` ou `env`.
6. Para SFTP, fingerprint SHA256 conferida com o provedor. Enter deixa a confirmação pendente.
7. Para keyring/age, opção de cadastrar a senha em prompt local oculto, com confirmação. A senha não deve ser digitada no chat, em argumento de comando, YAML ou documentação.

Cada domínio é independente, mesmo que compartilhe o servidor. Não há busca/importação automática de senhas do FileZilla. Por padrão, a configuração fica em `~/.mcp-locaweb-sftp/sites.yaml` e `settings.yaml`, fora do repositório e da pasta publicada. `--sites`, `--settings` e `--state-dir` são opções globais, antes do comando, para instalações com caminhos próprios.

O assistente cria o site com `publish_enabled: false`, não testa conexão e não envia arquivos. Configurações globais existentes são preservadas; uma configuração nova também começa com publicação desativada. Um domínio já cadastrado não é sobrescrito.

No SFTP sem fingerprint confirmada, não se pede a senha ainda. Depois:

```text
python -m mcp_locaweb_sftp scan-key exemplo.com.br
python -m mcp_locaweb_sftp set-key exemplo.com.br --fingerprint SHA256:CHAVE_CONFIRMADA --confirm
python -m mcp_locaweb_sftp credential exemplo.com.br
python -m mcp_locaweb_sftp testar exemplo.com.br
```

`scan-key` só consulta a chave observada, sem autenticar ou confiar nela. Confira-a por canal independente antes de `set-key`; o exemplo acima usa um marcador, não uma fingerprint válida. `testar` autentica e verifica a raiz, sem publicar.

Se o cofre falhar, o cadastro fica salvo, desativado e com `credential_status: pending` no resultado. Corrija o cofre e execute `credential`; não há fallback para senha em claro. `stored` significa gravação no cofre, não autenticação comprovada. Se cancelar o prompt depois de gravar o cadastro, ele também pode permanecer pendente; consulte `info`.

`age` pede os caminhos privados e a chave pública se ainda não estiver configurado. Exige instalação e identidade previamente preparadas, não gera chaves nem instala o executável. Cofre e identidade devem ficar fora da pasta do site. Em `env`, o comando informa o nome exato da variável a ser injetada pelo cofre do ambiente; não armazena a senha. Veja [configuração e credenciais](configuration-credentials.md).

## Comandos

| Comando | Efeito |
|---|---|
| `list` / `listar` | Lista domínios sem abrir conexões nem ler senhas |
| `info DOMINIO` | Mostra metadados de configuração, sem senha |
| `setup` / `configurar` | Cadastro interativo de um novo domínio |
| `register DOMINIO --entry-file ARQUIVO` | Cadastro a partir de um YAML/JSON validado contendo somente esse domínio; desativa publicação |
| `credential DOMINIO` | Senha em prompt oculto, ou orientação para variável de ambiente |
| `scan-key`, `set-key` | Consulta e registro explícito da identidade SSH |
| `test` / `testar DOMINIO` | Testa conexão e raiz sem alterar arquivos remotos |
| `compare` / `comparar DOMINIO` | Compara conteúdo e datas, sem envio |
| `preview` / `previa DOMINIO` | Mesma análise, incluindo o hash da prévia |
| `backup DOMINIO` | Baixa e verifica o inventário remoto, sem alterá-lo |
| `deploy` / `enviar DOMINIO --preview-hash HASH --confirm` | Executa a prévia confirmada, sujeito às demais travas |
| `migrate` / `migrar --from ORIGEM --to DESTINO` | Expõe a migração isolada da etapa 3; não abre conexão nem migra senhas |

Saídas operacionais são JSON em stdout. Perguntas ficam em stderr; ajuda é texto. Códigos de saída: `0` sucesso, `1` erro, `2` argumentos inválidos, `3` conflito, `4` falha parcial. O cadastro pode retornar sucesso com credencial pendente: leia os campos `credential_status` e `connection_tested`.

## Publicar uma prévia revisada

Depois de concluir e testar o cadastro, habilite explicitamente `publish_enabled: true` no site e em `settings.yaml`. Faça isso antes de gerar a prévia, pois a configuração participa do hash. Para FTPS, também é necessário `ftps_write_preconditions_confirmed: true`, somente após confirmar confinamento da conta e ausência de escritores concorrentes no servidor. Essa flag é uma declaração administrativa, não uma prova técnica dessas condições.

```text
python -m mcp_locaweb_sftp previa exemplo.com.br
python -m mcp_locaweb_sftp enviar exemplo.com.br --preview-hash HASH_DA_PREVIA_REVISADA --confirm
```

O hash SHA-256 vincula a prévia ao domínio, configuração, caminhos, inventários e hashes/datas dos arquivos. Nesta etapa ele **não é um token secreto, não expira e não é consumido**. O token de uso único válido por cinco minutos será acrescentado na etapa 5. Não integrar ainda este comando como publicação autônoma por IA.

O fluxo atual:

1. Exige confirmação e as flags de publicação antes de ler a credencial na CLI.
2. Adquire trava local por protocolo/servidor/porta e recalcula a prévia. Qualquer conflito, arquivo bloqueado ou divergência de hash impede o lote inteiro.
3. Cria registro privado e copia os arquivos locais aprovados para um snapshot, verificando seus hashes.
4. Baixa e verifica **todos** os originais que serão substituídos antes de iniciar qualquer upload.
5. Recalcula a prévia após o backup. Revalida snapshot e backup em disco antes de cada envio.
6. Registra intenção de criar diretório/enviar arquivo antes da operação; verifica novamente o estado anterior do arquivo remoto antes de substituir.
7. Lê o conteúdo remoto após o envio e confere SHA-256. Só então acrescenta o arquivo à lista `uploaded`.

Não há exclusão remota, remoção de arquivo parcial nem rollback automático. Hashes iguais dispensam envio. Arquivo diferente com data remota igual/mais recente, incluindo tolerância de dois segundos, bloqueia o lote. Regras adicionais de bloqueio por site somam-se à lista mínima.

## Registros e recuperação

Por padrão, cada execução fica em `~/.mcp-locaweb-sftp/state/runs/DOMINIO/ID/`, separada da pasta publicada:

- `deploy-result.json`: plano, fase, arquivo ativo, diretórios criados e arquivos com upload verificado.
- `sources/`: snapshot das versões locais enviadas.
- `backup/files/`: versões remotas anteriores.
- `backup/backup-manifest.json`: recibo de hash dos **arquivos selecionados**, não atestado independente de um snapshot completo e simultâneo do servidor.
- `backup-result.json`: resultado final do comando de backup completo. Exija `status: success`; um manifesto isolado não substitui esse resultado.

Se o processo morrer ou faltar disco depois da escrita remota, o último registro durável pode conter apenas a intenção de envio. `partial` é conservador: pode significar escrita parcial, envio concluído sem registro final ou tentativa cujo resultado precisa ser verificado. Não repetir cegamente. Consulte o servidor e o backup, gere uma nova comparação e resolva os conflitos antes de reenviar. Restauração é manual e exige uma decisão separada.

Uma interrupção forçada pode deixar a trava local. Confirme que não há processo executando antes de remover essa trava manualmente. O programa não a remove automaticamente por idade.

## Limites que continuam valendo

- A substituição é no próprio arquivo, não uma transação por site. Leitores podem observar conteúdo parcial durante um upload; use janela operacional apropriada.
- A trava protege somente processos que usam a mesma pasta de estado. Outros clientes, aplicações e computadores não são bloqueados. Revalidações não eliminam a corrida entre a última leitura e a escrita: mantenha o destino sem escritores concorrentes.
- FTPS não oferece criação exclusiva portátil e pode ocultar links. Exige restrição no servidor; veja [limites dos transportes](backends.md).
- SHA-256 exige leitura completa dos arquivos, várias vezes. Inventários grandes custam tráfego e tempo; não há comparação apenas por tamanho nem promessa de tempo fixo.
- Backup cobre arquivos, não banco de dados, permissões, horários originais ou diretórios vazios. Não equivale a backup integral da hospedagem nem a snapshot transacional.
- Backups, snapshots, configurações e logs são privados. No POSIX, novos arquivos usam 0600 e diretórios 0700. No Windows herdam ACLs; esta versão não as cria/audita. Pastas já existentes devem ter permissões adequadas.
- Nomes bloqueados não detectam todos os segredos embutidos em arquivos HTML/JS. Revise o conteúdo antes de habilitar publicação.
- Não houve operação em hospedagem real. Cofres nativos foram simulados; age e os servidores locais SFTP/FTPS foram executados de fato. A matriz de outros sistemas ainda está pendente.

Referência da CLI: [Click — comandos e grupos](https://click.palletsprojects.com/en/stable/commands-and-groups/).
