# Backends Python — etapa 2

Esta camada é uma biblioteca de transporte em desenvolvimento. Ela não seleciona sites reais, não lê os cadastros da instalação Windows e não substitui a skill operacional. CLI, armazenamento de credenciais, backup coordenado e autorização de deploy pertencem às próximas etapas.

## Interface comum

`SFTPBackend` e `FTPSBackend` implementam `Backend`. A construção abre a conexão e valida a raiz; usar `with` ou `close()` para liberar os recursos. Não há pool nem suporte a compartilhar uma conexão entre threads.

| Operação | Contrato |
|---|---|
| `check_connection()` | Verifica a raiz absoluta e seus ancestrais; não cria diretórios |
| `inventory()` | Retorna `FileState` ordenados, com SHA-256 e data UTC; rejeita objetos sem metadados confiáveis |
| `download(path, destination)` | Grava bytes em stream fornecido pelo chamador; retorna SHA-256; destino parcial deve ser descartado se ocorrer erro |
| `checksum(path)` | Lê todo o arquivo e calcula SHA-256 sem guardar seu conteúdo |
| `mkdir(path)` | Cria um diretório; os pais precisam existir e passar pela validação |
| `upload(path, source, expected_sha256=...)` | Valida o conteúdo de origem, cria arquivo novo e verifica SHA-256 lendo-o de volta; recusa caminho já existente |
| `close()` | Fecha a conexão; não remove arquivos |

As operações recebem caminhos relativos à raiz, com `/`, e usam a validação lexical do núcleo. A raiz é um caminho POSIX absoluto, sem barra final, exceto `/`. Não se concatena caminho em comando de shell. FTP usa comandos do protocolo; caracteres de controle e injeção de nova linha são rejeitados pela validação.

Para arquivos, tamanho e data de modificação são obrigatórios. A coleta remota passa pelos mesmos controles de caminho que a transferência. A comparação do núcleo continua responsável por detectar colisões de nomes entre sistemas, conflitos de data e o plano de candidatos; um inventário isolado não autoriza envio.

## Identidade e autenticação

**SFTP:** exige `fingerprint` no formato OpenSSH `SHA256:...`, confirmado previamente por canal independente. A política de chave compara o hash da chave recebida antes da autenticação. Não usa `AutoAddPolicy`, confiança no primeiro contato, agente SSH ou descoberta automática de chaves locais. Esta etapa suporta usuário/senha em memória; autenticação por chave privada pode ser acrescentada quando houver necessidade definida.

**FTPS:** implementa FTP explícito sobre TLS, porta padrão 21. Usa `ssl.create_default_context`, valida cadeia e nome do servidor, exige TLS 1.2 ou superior e ativa `PROT P` no canal de dados. Um `ca_file` opcional acrescenta uma âncora de confiança explícita, sem desabilitar a checagem de nome. Não oferece FTP sem TLS, downgrade nem FTPS implícito. Certificado autoassinado desconhecido é rejeitado.

Os construtores recebem credenciais apenas para autenticação, sem persistência própria. Bibliotecas de protocolo podem retê-las durante a sessão; não há promessa de apagamento seguro da memória. Não ativar logs de depuração de protocolos com credenciais reais. Cadastro, seleção exata de domínio, cofre e tratamento de mensagens sensíveis serão integrados nas etapas 3–5.

## Integridade e criação de arquivos

O SHA-256 remoto é obtido lendo os bytes pela conexão autenticada, tanto em SFTP quanto em FTPS. Assim não dependemos de extensão de checksum nem de acesso a shell no servidor. O custo é transferir integralmente cada arquivo lido e reler cada upload. Erros não são convertidos em hash vazio nem ignorados.

Antes de escrever, `upload` copia o stream de origem para um snapshot e verifica o SHA-256 esperado. Até 8 MiB ficam em memória; acima disso é usado um arquivo temporário, fechado/removido ao sair da operação. O snapshot elimina alterações do stream de origem entre a checagem e o envio. Depois do envio, o conteúdo remoto é lido e comparado ao hash esperado.

A lista mínima de bloqueios do núcleo vale para uploads e criação de diretórios. Regras adicionais por site serão fornecidas pelo coordenador após carregar a configuração. Arquivos remotos sensíveis podem precisar de leitura em um backup autorizado; não são apagados nem sobrescritos pelo transporte.

Não há API de exclusão, renomeação ou substituição nesta etapa. `upload` rejeita arquivos existentes, inclusive quando aparecem durante a preparação do snapshot. SFTP acrescenta abertura exclusiva (`SSH_FXF_EXCL`) para proteger a criação contra outro arquivo surgindo depois da checagem.

Se ocorrer falha depois de iniciar a escrita, pode restar um arquivo parcial. A exceção é propagada; não se apaga o arquivo nem se declara rollback. A conexão FTPS é fechada em falhas de leitura/escrita que possam deixar respostas pendentes no canal de controle. O registro estruturado de falha parcial, backup antes de substituir e a política de recuperação serão responsabilidade da etapa 4.

## Limites de confinamento e concorrência

- SFTP usa `lstat` para rejeitar links e objetos especiais; confere o caminho canônico dos diretórios e revalida os pais antes das operações.
- FTPS exige `MLSD`. Sem essa extensão ou sem tipo/tamanho/data suficientes, a operação falha; não se tenta interpretar texto livre de `LIST`.
- Tipos de link expostos pelo FTP e bits de tipo Unix, quando fornecidos, são rejeitados. Alguns servidores seguem links e os anunciam como arquivos normais; o cliente não consegue detectar isso com garantia. **A conta FTPS deve estar confinada pelo servidor à área autorizada**, e a configuração dessa área precisa ser verificada pelo administrador.
- FTP não tem criação exclusiva portável equivalente à do SFTP. Um arquivo criado por outro processo entre a última checagem e `STOR` pode ser sobrescrito. O backend FTPS exige uma área sem escritores concorrentes; **não está liberado como deploy de produção enquanto o fluxo de publicação não tratar essa precondição**. Uma trava só entre clientes desta ferramenta não impede mudanças feitas por FTP, painel ou aplicação externa.
- Revalidação de caminhos e de metadados reduz riscos, mas não é uma transação. Links trocados entre comandos, hardlinks não expostos e mudanças de mesmo tamanho/data podem escapar dessas verificações. Não há proteção contra um servidor malicioso que já detenha a identidade confiada.
- Cada operação de rede usa timeout finito. Um timeout por operação não limita a duração total de um inventário grande; cancelamento e limites da ferramenta pública serão tratados na integração.

## Testes executados

Instalação de desenvolvimento, a partir da raiz do repositório:

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=mcp_locaweb_sftp --cov-branch --cov-report=term-missing --cov-fail-under=81
```

`requirements.txt` contém a dependência de execução Paramiko. `requirements-dev.txt` acrescenta os testes e o servidor FTPS. Não é necessário WinSCP para o código Python.

Os servidores de teste escutam apenas em loopback, em portas efêmeras. O SFTP implementa `paramiko.ServerInterface` e `SFTPServerInterface`, usando arquivos temporários e links simulados sem exigir privilégios de symlink no Windows. O FTPS usa `pyftpdlib` e certificado/chave gerados em diretório temporário. As fixtures de `tests/conftest.py` podem ser reutilizadas nas etapas 3–5. Nenhuma chave privada está incluída no repositório.

`tests/test_backends.py` verifica:

- Transferência binária com múltiplos blocos, arquivo vazio, Unicode, espaços e colchetes literais; inventário com data UTC.
- Chave SSH divergente rejeitada antes de autenticar, senha incorreta, certificado desconhecido, nome TLS diferente e timeout de conexão.
- Caminhos perigosos, arquivos bloqueados, links, falta de permissão, metadados incompletos e listagens ambíguas.
- Hash de origem diferente sem escrita remota, corrupção após envio, mudança durante leitura, criação concorrente e escrita local incompleta.
- Interrupção deixando arquivo parcial, recusa de substituição e rejeição de servidor FTPS sem `MLSD`.

Em 26/09/2026: **236 testes aprovados**, cobertura combinada de instruções e ramos de **100% sobre o código Python atual**, Windows/Python 3.14.3. O teste PowerShell legado passou. Isso não cobre as etapas ainda não implementadas, não é ensaio contra a hospedagem real e não comprova execução em Linux/macOS ou Python 3.11.

## Referências técnicas

- [Paramiko SSHClient e política de chaves](https://docs.paramiko.org/en/stable/api/client.html).
- [Paramiko SFTP e abertura exclusiva](https://docs.paramiko.org/en/stable/api/sftp.html).
- [Paramiko ServerInterface](https://docs.paramiko.org/en/stable/api/server.html).
- [Python ftplib / FTP_TLS](https://docs.python.org/3/library/ftplib.html).
