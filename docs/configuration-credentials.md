# Configuração, credenciais e migração — etapa 3

A implementação Python carrega configurações, seleciona um provedor de credenciais e abre uma conexão cadastrada. A etapa 4 acrescentou a [CLI](cli.md), incluindo cadastro interativo, `migrate` e coordenação de backup/deploy; a etapa 5 acrescentou [MCP via stdio e token efêmero](mcp-setup.md). A instalação PowerShell existente não foi alterada. As evidências de testes ao final deste documento correspondem à entrega da etapa 3; os resultados atuais estão no [plano](migration-plan.md).

## Configuração sem segredos

Os exemplos estão em [sites.example.yaml](../examples/sites.example.yaml) e [settings.example.yaml](../examples/settings.example.yaml). Copie-os para um diretório privado, preferencialmente fora do checkout. Nunca coloque senha, token ou chave privada no YAML.

`load_sites(path)` aceita o YAML novo ou o `sites.json` legado. O formato JSON é interpretado como cadastro legado; não é uma alternativa para salvar o esquema novo. `load_settings(path)` faz o equivalente para as configurações gerais. Os modelos Pydantic rejeitam campos desconhecidos e tipos incorretos: `"false"`, por exemplo, não substitui o booleano `false`. Publicação começa desativada tanto no site quanto nas configurações gerais.

O carregador rejeita chaves duplicadas, domínios que colidem após normalização, tags executáveis, âncoras/aliases YAML e documentos acima de 1 MiB. Há limites de profundidade e quantidade de tokens YAML. Não há interpolação de variáveis de ambiente no conteúdo nem carregamento dinâmico de classes. As mensagens públicas dos carregadores não repetem valores do documento, inclusive em erros de campo desconhecido.

| Campo do site | Regra |
|---|---|
| `protocol` | `sftp` ou `ftps`; FTP em claro não é suportado |
| `host`, `port`, `user` | Host DNS/IP, porta inteira válida e usuário explícito; porta padrão 22/21 conforme protocolo |
| `local_root` | Caminho absoluto ou `~/...`; caminhos de outro SO são preservados no cadastro, mas precisam de mapeamento explícito antes de uso local |
| `remote_root` | Raiz POSIX absoluta; sem travessia, ambiguidades ou barra final, exceto `/` |
| `ssh_fingerprint` | Uma fingerprint OpenSSH SHA256; `null` permite cadastro pendente, mas bloqueia conexão SFTP |
| `ca_file` | Opcional, somente para FTPS; caminho de CA confiável, sem desabilitar verificação TLS |
| `credential_store` | `keyring`, `age` ou `env`; padrão `keyring` |
| `publish_enabled` | Padrão `false`; o coordenador exige opt-in no site e nas configurações globais |
| `ftps_write_preconditions_confirmed` | Padrão `false`; para envio FTPS exige confirmação administrativa de confinamento e ausência de escritores concorrentes |
| `blocked_paths` | Regras adicionais; não removem os bloqueios mínimos do núcleo |

`Settings` contém `schema_version: 1`, `publish_enabled: false`, `timeout_seconds` finito entre mais de zero e 120 segundos, e o bloco opcional `age`. UNC não é suportado nesta etapa. Caminhos de configuração/cofre são verificados para rejeitar links e reparse points, incluindo junções Windows.

## Vínculo da credencial

`CredentialKey.for_site(domain, site)` gera um identificador SHA-256 sobre domínio, protocolo, host, porta, usuário, raiz remota, fingerprint SSH e caminho da CA TLS. Não é um hash da senha. Mudança nesses dados exige novo cadastro da credencial. Alterar somente a pasta local não troca a credencial.

O cofre e o age guardam um envelope com versão, vínculo e senha. Copiar esse envelope para a chave de outra conexão não o torna válido. A variável de ambiente usa o mesmo identificador no nome. Configure e confirme a identidade do servidor antes de cadastrar a senha, para evitar recadastro quando a fingerprint for preenchida.

Os provedores retornam `SecretStr`: representação textual e `repr` mascaram o valor. O código só extrai a senha para autenticar ou entregá-la ao mecanismo de armazenamento. Isso reduz exposição acidental; **não apaga a senha da memória nem protege contra um depurador, administrador ou processo comprometido**.

## Provedores disponíveis

### Cofre do sistema (`keyring`)

Seleciona diretamente o backend nativo: Windows Credential Locker, macOS Keychain ou Secret Service no Linux. Usa a biblioteca keyring, sem chamadas DPAPI próprias. Não usa descoberta genérica de plugins, `ChainerBackend`, backend em texto puro ou fallback automático. Se o cofre estiver indisponível/bloqueado, a operação falha.

Linux precisa do serviço Secret Service e sessão D-Bus apropriados. KWallet não está implementado nesta seleção. As credenciais do cofre continuam pertencendo ao usuário/SO; **portabilidade do programa não significa que o cofre nativo possa ser copiado entre máquinas**. Para migração de credenciais entre ambientes, use age ou faça novo cadastro.

As entradas usam o namespace `mcp-locaweb-sftp/v1` e o identificador da conexão. `KeyringStore.set/get` não acessa as entradas de outros aplicativos. Limites de tamanho do cofre nativo podem ser menores que os 16.384 caracteres aceitos pelo validador; a falha é reportada sem substituir o provedor.

### Arquivo criptografado (`age`)

Usa o executável age instalado e confiável, indicado por caminho absoluto. Não baixa executáveis automaticamente. O bloco `age` em `settings.yaml` contém apenas:

- `directory`: pasta privada já existente para os arquivos criptografados;
- `executable`: caminho absoluto do age;
- `identity`: caminho do arquivo de identidade privada, necessário para ler;
- `recipient`: chave pública X25519 `age1...`, necessária para gravar.

Somente identidades/recipients X25519 nativos são aceitos; plugins, identidades SSH e prompts interativos de passphrase não estão implementados. SOPS não foi implementado: age é a opção criptografada desta etapa.

Senha/envelope passam por pipes, nunca por argumentos da linha de comando, arquivo temporário em claro ou saída exibida. Os processos têm timeout e são iniciados sem janela no Windows. `stderr`, erro do processo e saída parcial de descriptografia não são incluídos nas exceções públicas.

O arquivo persistido é `<vínculo>.age`. A gravação prepara o ciphertext, sincroniza o arquivo temporário e faz substituição atômica; se a criptografia falhar, a versão anterior não é tocada. Isso não equivale a bloqueio entre múltiplos escritores: usar uma pasta privada sem gravações concorrentes.

No POSIX, diretório e identidade não podem ter permissões de grupo/outros, e arquivos novos usam modo 0600. No Windows, os arquivos herdam a ACL da pasta; esta versão **não cria nem audita ACLs Windows**. Use uma pasta protegida do perfil do usuário. Identidade e executável são recursos locais confiáveis; validações de caminho não eliminam corridas contra alterações feitas por outro processo com acesso a essa pasta.

O programa guarda senha em memória durante a operação. A identidade privada é um segredo separado, que também precisa de proteção e cópia segura fora do repositório. Arquivos `.age`, `.identity` e diretórios de dados privados estão ignorados pelo Git; isso não dispensa revisar o conteúdo antes de publicar.

### Variável de ambiente (`env`)

Somente leitura. O nome exato é `MCP_LOCAWEB_PASSWORD_` seguido do identificador completo da conexão em hexadecimal maiúsculo, disponível em `CredentialKey.env_name`. Não existe variável global de senha, aproximação de domínio ou busca em `.env`.

A variável deve ser injetada pelo ambiente/cofre do CI. Não gravá-la em arquivos versionados ou histórico de comandos. **Ambiente é texto em memória, não criptografia**; processos autorizados podem lê-lo e filhos podem herdá-lo. O provedor não persiste o valor nem modifica o ambiente.

## Integração com a conexão

`open_site(sites, domain, settings=...)` exige correspondência exata de domínio cadastrado, verifica a prontidão da fingerprint e seleciona o provedor declarado. Um provedor passado explicitamente com tipo diferente é rejeitado. Não tenta outra senha/provedor se a leitura ou autenticação falhar. Erros de conexão são apresentados sem os diagnósticos brutos, que poderiam incluir informações sensíveis.

O retorno é a conexão de baixo nível. Isso **não é autorização de deploy**: `publish_enabled`, regras adicionais do site, backup, conflito de datas e registros de falha são aplicados pelo coordenador da etapa 4. Não usar os métodos de transporte diretamente como substituto dessa política. Para novos usuários, o [assistente `setup/configurar`](cli.md#primeira-instalação-perguntas-por-site) pergunta os dados de cada site e cadastra a senha por prompt oculto.

## Migração implementada e testada

`migrate_legacy(source, destination, local_roots=...)` recebe a raiz da skill antiga e uma pasta de destino nova, fora da origem. Lê somente `config/sites.json` e `config/settings.json`. Não varre outras pastas, abre sessões de rede, acessa FileZilla, lê `.dpapi` ou altera a instalação anterior.

A função:

1. Valida todos os cadastros e o destino antes de criar arquivos.
2. Converte nomes como `localRoot`/`remoteRoot`/`username` para o esquema Python.
3. Aceita uma única fingerprint SHA256 já cadastrada; remove apenas o prefixo de tipo/tamanho usado pelo WinSCP. Fingerprint ausente fica pendente. Wildcard, MD5 e listas de chaves são rejeitados; a migração não confirma uma chave por consulta de rede.
4. Aplica os caminhos locais explicitamente fornecidos em `local_roots`, sem adivinhar a correspondência Windows/Linux/macOS.
5. Grava `sites.yaml` e `settings.yaml` com publicação desativada, mesmo que estivesse ativa no legado.
6. Grava por último `migration.json`, com todos os domínios pendentes de recadastro de senha e os que ainda precisam de fingerprint.

**As senhas DPAPI não são migradas automaticamente.** A opção implementada é recadastrá-las no provedor escolhido. Isso evita dependência do Windows/usuário originais e exportação de senha em claro. O relatório descreve pendências no momento da migração; não é um monitor do estado atual do cofre.

Destino existente é recusado. Se ocorrer erro durante a gravação, o destino incompleto permanece para inspeção e não se retorna sucesso. O relatório final só é criado após os dois YAMLs. Não há rollback destrutivo nem tentativa de sobrescrever esse destino numa nova execução. A publicação exclusiva dos arquivos usa hard links; em sistema de arquivos sem esse recurso a operação falha, preservando a origem.

## Evidências e limites dos testes

Os testes desta etapa usam configurações e credenciais fictícias. Cobrem rejeição de YAML/JSON ambíguo, campos de segredo no cadastro, migração em diretório isolado, origem intacta, publicação desativada, erro de disco, vínculos trocados, erro de autenticação, erros sem conteúdo secreto e ausência de fallback.

Em 26/09/2026, a suíte completa passou com **330 testes, sem skips na execução com age disponível**, e **96,78% de cobertura combinada de instruções e ramos**. Ambiente Windows/Python 3.14.3, Pydantic 2.13.5, PyYAML 6.0.3 e keyring 25.7.0. O teste PowerShell anterior também passou. A sintaxe dos arquivos foi conferida com a gramática de Python 3.11; execução nesse interpretador permanece pendente.

O age **v1.3.2 real** foi executado no Windows, com pacote da [distribuição oficial](https://github.com/FiloSottile/age/releases/tag/v1.3.2) conferido contra o SHA-256 disponibilizado pelo GitHub. Testes criptografam/descriptografam dados fictícios e rejeitam adulteração. O teste de integração abre FTPS local com senha do age. Outro teste migra um cadastro, recadastra uma senha num cofre simulado e abre SFTP local.

O cofre nativo foi simulado nos testes de leitura/escrita; não foram cadastradas senhas no cofre real do usuário. A seleção da classe nativa foi conferida no Windows, mas integração real com Windows Credential Locker, macOS Keychain e Secret Service continua pendente. Não afirmar validação multi-SO com base nestes testes.

Para incluir os testes reais de age, instale age/age-keygen de fonte confiável no mesmo diretório e disponibilize `age` no PATH ou indique seu caminho pela variável **de testes** `MCP_LOCAWEB_TEST_AGE`. Sem o executável, esses testes aparecem como **skipped**, não como validação criptográfica realizada. A instalação age usada no desenvolvimento ficou dentro do ambiente virtual ignorado pelo Git.

Referências: [backends e configuração de keyring](https://keyring.readthedocs.io/en/latest/), [modelos Pydantic](https://docs.pydantic.dev/latest/concepts/models/) e [age](https://github.com/FiloSottile/age).
