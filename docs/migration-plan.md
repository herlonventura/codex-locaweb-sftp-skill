# Migração para Python e MCP — registro das oito etapas

## Estado

As oito etapas da sequência aceita foram entregues em 26/09/2026, incluindo a documentação da etapa 8. Os scripts Windows existentes e a skill operacional continuam usando seu fluxo original. O Python contém núcleo, transportes, configuração, provedores de credenciais, migrador, CLI, servidor MCP stdio, pacotes, Docker e binários. Os números nas seções de cada etapa são evidências históricas daquela entrega. Conclusão das etapas não significa publicação no PyPI, migração operacional ou homologação nos aplicativos/cofres reais; essas pendências estão discriminadas abaixo.

O alvo é Python 3.11+. A etapa 7 comprovou execução em Windows, Linux e macOS com Python 3.11 e 3.14, além das verificações de distribuição e Docker. A execução local também foi verificada em Windows/Python 3.14.3. [Matriz e artefatos](distribution.md).

## Etapa 1: contrato do núcleo

| Módulo | Responsabilidade |
|---|---|
| `core/checksum.py` | SHA-256 sobre bytes ou blocos fornecidos pelo chamador; validação de digest hexadecimal completo |
| `core/guards.py` | Seleção exata de domínio, validação lexical de caminhos relativos e bloqueios de publicação |
| `core/compare.py` | Inventários de arquivos imutáveis, detecção de colisões e classificação de diferenças |

Nenhuma dessas funções abre arquivos, consulta o relógio, acessa variáveis de ambiente, lê credenciais, conecta à rede ou executa transferências. Datas e conteúdo são fornecidos pelo chamador.

### Domínios, hashes e datas

- `require_registered_domain` aceita apenas uma chave de domínio completo já cadastrada. Maiúsculas e espaços externos são normalizados; grafia, subdomínio, IP, URL e porta não são corrigidos ou inferidos. Domínios internacionais devem usar a forma ASCII/punycode explicitamente cadastrada.
- `sha256_bytes` e `sha256_chunks` calculam sobre os bytes exatos, sem modificar codificação ou quebras de linha. Não aceitam um caminho de arquivo no lugar do conteúdo.
- `FileState` exige caminho, SHA-256 completo e data com fuso explícito. A data é convertida para UTC; não se usa implicitamente o fuso do computador.
- Hashes iguais dispensam envio independentemente da data. Para hashes diferentes, a cópia local precisa ser **estritamente mais de 2 segundos mais recente** que a remota. Diferenças de exatamente 2 segundos ainda são conflito, como no código PowerShell original.

### Caminhos e bloqueios

Todos os inventários usam caminhos de arquivos relativos com `/`. Rejeitamos caminhos absolutos, barras invertidas, segmentos vazios, `.`/`..`, controles Unicode, nomes de dispositivos Windows, caracteres não portáveis e espaços/pontos ambíguos nas bordas dos segmentos. O núcleo não corrige silenciosamente um caminho recebido.

Diferenças apenas de maiúsculas ou normalização Unicode, inclusive em diretórios implícitos, são rejeitadas. Também é rejeitado um caminho usado como arquivo em um inventário e como diretório no outro. Essa escolha é conservadora: alguns nomes seriam válidos em Linux, mas poderiam se sobrepor em Windows/macOS.

Os bloqueios existentes foram preservados e incluem `.git`, `.env`, `*.key`, `*.pem`, `wp-config.php`, `backups`, configurações de servidor e scripts PowerShell. Foram acrescentados `.env.*`, `.htpasswd` e nomes usuais de chaves SSH. Uma lista de bloqueios por nome não identifica todo possível segredo dentro de arquivos; revisão do conteúdo continua necessária antes da publicação.

Regras adicionais por site são **aditivas**: não removem a lista mínima. A correspondência ignora maiúsculas de forma igual em todos os sistemas:

- Regra de um segmento, como `*.sql`, vale em qualquer profundidade.
- Regra com vários segmentos, como `private/*.json`, parte da raiz e exige a mesma profundidade; `*` e `?` não atravessam `/`.
- Barra final restringe diretórios e seus descendentes: `private/` em qualquer profundidade, ou `downloads/internal/` a partir da raiz.
- `**`, classes como `[ab]`, padrões absolutos e travessia de diretórios são rejeitados. Uma regra inválida bloqueia a comparação, em vez de ser ignorada.

`Comparison` informa `equal`, `modified`, `new_local`, `remote_only`, `conflicts` e `blocked`. Apenas arquivos novos ou diferentes são candidatos a envio. Um arquivo sensível igual ou existente apenas no servidor permanece intocado. Um único conflito ou candidato bloqueado faz `upload_paths` retornar vazio para o lote inteiro. Não há plano de exclusão.

`upload_paths` contém candidatos, **não uma autorização de publicação**. A coleta futura deve inspecionar arquivos reais, rejeitar links/junções, conferir a raiz, obter hashes confiáveis e revalidar alterações concorrentes. Validação lexical não inspeciona links, permissões, montagem de volumes ou corridas entre processos. As etapas seguintes ainda precisarão exigir opt-in de publicação, aprovação, backup e verificação pós-upload.

## Testes desta etapa

Os testes `test_checksum.py`, `test_guards.py` e `test_compare.py` usam dados fictícios em memória. Cobrem hashes conhecidos, fronteira de 2 segundos, fusos, falta de metadados, diretórios conflitantes, duplicatas, nomes ambíguos entre sistemas, regras adicionais e bloqueio integral do lote. Há também um teste que executa o núcleo com abertura de arquivos e criação de sockets proibidas.

Em um ambiente virtual Python, instale `requirements-dev.txt` e execute, a partir da raiz do repositório:

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=mcp_locaweb_sftp --cov-branch --cov-report=term-missing --cov-fail-under=81
```

O `pytest.ini` aponta para `src/`, portanto ainda não é necessário instalar o projeto como pacote. O requisito mínimo é cobertura maior que 80% acompanhada de testes comportamentais; cobertura sozinha não demonstra segurança.

O teste PowerShell existente continua em `tests/test-deploy-local.ps1`. Ele usa um destino simulado e não acessa a hospedagem.

Resultado local em 26/09/2026: **149 testes Python aprovados, 100% de cobertura de instruções e ramos do núcleo novo**, com pytest 9.1.1 e pytest-cov 7.1.0. O teste PowerShell também passou. A sintaxe foi conferida com a gramática Python 3.11; execução nesse interpretador e em outros sistemas ainda não foi realizada. Nenhum site real foi acessado pelos testes.

## Etapa 2: transportes e servidores simulados

Implementados `backends/base.py`, `backends/sftp.py` e `backends/ftps.py`, com interface comum para conexão, inventário, download, SHA-256, criação de diretório e envio de arquivo novo. SFTP exige fingerprint SHA256 confirmada antes da autenticação; FTPS exige TLS validado no controle e nos dados. Os dois calculam o SHA-256 remoto lendo o conteúdo completo, sem comandos de shell ou dependência de extensão proprietária do servidor.

Os servidores em `tests/conftest.py` usam loopback e portas dinâmicas: SFTP com `paramiko.ServerInterface` e FTPS com `pyftpdlib`. Chaves e certificados são gerados durante o teste; credenciais são fictícias e os arquivos ficam em diretórios temporários. As fixtures já podem ser reutilizadas nas etapas 3–5.

Resultado local: **236 testes Python aprovados**, com 100% de cobertura de instruções e ramos do núcleo e dos backends atuais. O teste PowerShell legado também passou. Ambiente: Windows, Python 3.14.3, Paramiko 4.0.0, pyftpdlib 2.2.0 e pyOpenSSL 26.4.0. A sintaxe do código foi conferida com a gramática de Python 3.11; a execução nesse interpretador e em Linux/macOS segue pendente.

Na entrega da etapa 2, o backend ainda não substituía arquivos. A etapa 4 acrescentou essa operação sob a coordenação de backup e publicação. Em FTPS, a checagem prévia de existência não é atômica, e alguns servidores ocultam links. [Contrato, evidências e limitações](backends.md).

## Etapa 3: configuração, credenciais e migração

Implementados modelos Pydantic imutáveis, carregamento restrito de YAML/JSON legado e seleção explícita entre cofre nativo, age e ambiente. As credenciais são vinculadas ao domínio e à identidade da conexão, retornam mascaradas e não entram no YAML. Não há fallback para arquivo em claro nem descoberta genérica de plugins de cofre.

`migrate_legacy` converte os cadastros para um destino novo, preserva a origem, aplica mapeamento explícito de pastas locais e grava publicação desativada. O relatório final indica senhas pendentes de recadastro e fingerprints ausentes. A migração não lê/descriptografa DPAPI; o recadastro é a estratégia adotada nesta implementação. Em erro de gravação, preserva o destino incompleto e não retorna sucesso. A etapa 4 exporá essa lógica na CLI; a etapa 8 documentará o fluxo final de uso.

Validação local: **330 testes aprovados, 96,78% de cobertura de instruções e ramos do código Python atual**. Testes com dados fictícios incluem migração isolada seguida de recadastro/conexão SFTP, criptografia age real, FTPS com senha age, adulteração, erro de disco, rejeição de configuração ambígua e proteção das mensagens de erro. O teste PowerShell legado passou. Windows/Python 3.14.3, Pydantic 2.13.5, PyYAML 6.0.3, keyring 25.7.0 e age 1.3.2. Sintaxe conferida com gramática de Python 3.11; execução nesse interpretador e em outros SOs continua pendente.

As operações do cofre nativo foram simuladas; nenhuma senha foi cadastrada no cofre real do usuário. age foi executado de fato com chaves temporárias. SOPS não está implementado; age atende à opção criptografada desta etapa. [Contrato e limitações](configuration-credentials.md).

## Etapa 4: CLI e coordenação das operações

Implementados `python -m mcp_locaweb_sftp`, aliases em português e `setup/configurar` para cadastro por perguntas, sem depender de FileZilla. Senha só em prompt local oculto, nunca argumento ou YAML. Cadastro novo começa desativado; falta de fingerprint ou cofre indisponível fica indicada como pendência. Migração da etapa 3 exposta pelo comando `migrate/migrar`.

O coordenador cria snapshots locais, exige backup de todos os arquivos a substituir antes de qualquer upload, revalida a prévia e confere SHA-256 após cada envio. Usa trava local por endpoint e diário de intenção antes de mutações; falhas parciais ficam registradas sem exclusão ou rollback automático. Caminhos locais rejeitam links, junções e hardlinks. Configurações privadas e arquivos age foram acrescentados à lista mínima de bloqueios.

Na entrega da etapa 4, a publicação exigia opt-in global e por site, `--confirm` e hash da prévia. A etapa 5 acrescentou o token efêmero também à CLI. O backend não substitui a política do coordenador; hash/token não comprovam consentimento humano.

FTPS exige `ftps_write_preconditions_confirmed`, uma declaração administrativa de confinamento e ausência de escritores concorrentes, não uma garantia do protocolo. A trava local não bloqueia outros computadores ou aplicações; escrita remota não é transacional. [Uso, cadastro e recuperação](cli.md).

Validação local: **405 testes aprovados, sem skips com age disponível; 95,13% de cobertura combinada de instruções e ramos**. Testes incluem cadastro do zero sem conexão, prompt oculto, pendências de cofre/fingerprint, prévia alterada, envio real aos servidores locais SFTP/FTPS, backup antes de substituir, truncamento de arquivo menor, conflito, corrupção, mudança remota antes de substituir, falha de disco após upload e manutenção do diário de intenção. O teste PowerShell legado, a conferência de dependências e a análise de sintaxe com gramática Python 3.11 passaram. Execução: Windows/Python 3.14.3, Click 8.5.0. Cofre nativo simulado, age real, nenhuma conta de hospedagem acessada. Execução em Python 3.11 e Linux/macOS permanece pendente.

## Etapa 5: servidor MCP e token efêmero

Implementado servidor stdio com `Server` do **SDK oficial mcp 2.2.0**, sete ferramentas com schemas Pydantic, respostas estruturadas e erros sanitizados. O processo não abre servidor HTTP. Credenciais, confiança em fingerprint/CA e habilitação de publicação não são expostas como ferramentas MCP. Cadastro MCP cria somente domínio novo desativado; a preparação de senha/identidade continua local.

Prévia sem bloqueios emite token aleatório de 256 bits, vinculado a domínio e hash de toda a prévia. O recibo privado SQLite guarda somente o hash do token. Consumo transacional impede reutilização entre processos, inclusive CLI/MCP compartilhando o mesmo estado. Validade de 300 segundos usa relógio UTC e monotônico; expiração, escopo diferente, token desconhecido ou consumido falham. O coordenador consome antes de recalcular a prévia; uma falha posterior não devolve o token. Confere novamente o prazo depois do backup e antes da primeira mutação.

O cliente continua responsável por obter autorização explícita do usuário; `confirm=true` é uma declaração técnica, não uma prova independente de consentimento. O token não protege contra um processo local que possa modificar o programa/configuração/banco privado. [Setup, chamadas, vínculo e limitações](mcp-setup.md).

Validação local: **443 testes aprovados, sem skips com age disponível; 94,16% de cobertura combinada de instruções e ramos**. Inclui cliente SDK real → processo stdio → SFTP/FTPS local; descoberta das ferramentas; prévia/backup/envio verificado; respostas de conflito e falha parcial; rejeição de valores sensíveis em argumentos; duas tentativas em processos distintos com um único consumo; fronteira de 300 segundos, mudanças de relógio, erro de disco e vencimento após backup sem upload. O teste PowerShell legado, `pip check` e a gramática Python 3.11 passaram. Ambiente Windows/Python 3.14.3; mcp/mcp-types 2.2.0, anyio 4.15.1. Os testes stdio em subprocesso passaram, mas sua execução não está somada à cobertura do processo principal. Nenhum site real foi acessado e nenhum segredo real foi cadastrado. Cofre nativo simulado; age real. Integrações nos aplicativos e execução multi-SO seguem pendentes.

## Etapa 6: testes ampliados de falha e recuperação

Acrescentados 24 cenários, incluindo encerramento abrupto de processo filho após escrita parcial real em SFTP/FTPS local; confirmação de backup intacto, diário de intenção, token consumido e trava residual; retomada com conflito; dois domínios no mesmo endpoint disputando publicação; falha no último backup antes de qualquer upload; código 4 na CLI; cancelamento cooperativo MCP sem liberação prematura da trava; isolamento de credenciais e ausência de fallback; corrupção/perda/hardlink do banco de tokens; mudanças e escritas incompletas durante snapshot local.

Os cenários passaram sem exigir mudanças no código de produção. A fixture de cadastro fictício foi compartilhada entre os testes de aplicação e MCP. O teste de encerramento recusa host fora de loopback; não há uso dos cadastros reais. O cancelamento cooperativo não desfaz a publicação: o worker pode concluir e gravar o resultado depois que o cliente cancela sua chamada. A interrupção forçada preserva a pendência para revisão manual. [Matriz, comandos de teste e evidência de recuperação](testing.md).

Validação local: **467 testes aprovados, sem skips com age disponível; 94,77% de cobertura combinada de instruções e ramos**. Windows/Python 3.14.3, mcp/mcp-types 2.2.0, anyio 4.15.1 e age 1.3.2. O teste PowerShell legado, `pip check` e análise de sintaxe pela gramática Python 3.11 passaram. Subprocessos possuem asserções próprias e não entram na cobertura do processo principal. Cofre nativo simulado, age executado de fato, nenhuma hospedagem real acessada. Não é teste de corte de energia nem de outras máquinas escrevendo no servidor. Linux/macOS e aplicativos MCP específicos continuam pendentes para as etapas seguintes.

## Etapa 7: pacote, binários, Docker e CI

Implementados `pyproject.toml`, comandos instaláveis `mcp-locaweb-sftp` e `mcp-locaweb-sftp-mcp`, wheel e sdist com lista restrita de conteúdo, instalação isolada por pip e pipx, binários PyInstaller produzidos no SO de destino e imagem Docker não-root. A imagem inclui age; binários nativos exigem age externo quando esse provedor for escolhido. Nenhum cadastro privado ou segredo acompanha os artefatos. Não houve publicação no PyPI/registro de imagens nem conexão com hospedagens reais.

O CI executa a suíte de 467 testes com Python 3.11 e 3.14 em Windows, Linux e macOS. Depois verifica o pacote instalado fora das fontes e, em Python 3.11, os binários: prévia CLI → envio MCP com token → backup/verificação → rejeição de reutilização, contra SFTP e FTPS locais. O job Docker verifica usuário 10001, CLI, age, inicialização/descoberta MCP e rejeição de envio incompleto. WSL e aplicativos MCP específicos não foram executados. Cofres nativos continuam simulados; age é real com identidades temporárias.

O primeiro ciclo completo ficou verde nos sete jobs: [execução 36275937220](https://github.com/herlonventura/codex-locaweb-sftp-skill/actions/runs/36275937220). A versão final do empacotamento preserva permissões/links de Linux/macOS em TAR.GZ e mantém ZIP no Windows. Resultados finais, arquiteturas, cobertura e acesso aos artefatos estão no [guia de distribuição](distribution.md). Nenhum código de produção da CLI, MCP ou transportes precisou ser alterado nesta etapa.

## Etapa 8: documentação e configurações de clientes

README reescrito com instalação Python como entrada principal e instruções anteriores preservadas em [legado Windows](legacy-windows.md). Acrescentados [instalação](install.md), [migração da skill](migration-from-codex-skill.md), [configuração de clientes MCP](mcp-clients.md) e [recuperação](recovery.md). Seis exemplos separados em JSON/YAML/TOML atendem aos formatos documentados de Claude Desktop, Cursor, Zed, VS Code, Continue e Codex, com fontes oficiais consultadas em 26/09/2026.

Validação desta etapa: seis exemplos parseados, argumentos de inicialização conferidos contra a CLI instalada, links relativos verificados e processo stdio iniciado com os argumentos do exemplo pelo cliente oficial do SDK. O teste descobriu as sete ferramentas, listou somente um cadastro fictício desativado e confirmou erro para catálogo vazio. Não leu senhas, não conectou a hospedagens nem gravou estado operacional. A revisão corrigiu a hipótese incorreta de que `{}` seria aceito como catálogo e os avisos antigos de matriz ainda pendente.

Nenhum código de produção, script legado ou instalação de cliente foi modificado. Não foram repetidos os testes de migração nem executada migração de produção: a lógica e sua validação pertencem às etapas 3–4. O CI de código da etapa 7 permanece como evidência; as verificações adicionais desta etapa são de documentação/configuração, não homologação dentro dos aplicativos.

## Pendências além da entrega documental

| Item | Estado real |
|---|---|
| Execução Linux/macOS/Windows | Comprovada pela matriz da etapa 7 |
| Integração MCP no protocolo stdio | Comprovada com cliente oficial do SDK e processos instalados/congelados |
| Claude Desktop, Cursor, Zed, VS Code, Continue e Codex | Codex App Server: inicialização e sete ferramentas reconhecidas; interfaces/aprovações pendentes |
| Keyring nativo em cada SO | Windows real com credencial fictícia e MCP instalado aprovado; Linux/macOS simulados |
| WSL e outras versões/arquiteturas | Não executados |
| Publicação no PyPI e em registro de contêineres | Não realizada; instalação disponível por fonte/wheel e build Docker |
| Licença de redistribuição do projeto | Não definida pelo autor; não foi inventada uma licença |
| Migração da instalação operacional | Não realizada; documentação pronta, conversão isolada já testada |
| Senhas DPAPI | Recadastro necessário, sem exportação/descriptografia automática |

## Sequência aceita

Melhoria posterior autorizada: [retenção automática de três envios concluídos por domínio](retention.md), comum à CLI e ao MCP Python. Dispara apenas após sucesso persistido; preserva backups completos, falhas e demais domínios. Exclui somente pastas locais antigas elegíveis, com verificação de caminhos/links e diário removido por último. Falhas de limpeza retornam aviso sem incentivar reenvio. [Evidência de testes](testing.md#retenção-automática-após-as-oito-etapas). Não altera os scripts PowerShell nem limpa instalações operacionais durante o desenvolvimento.

Validação posterior às oito etapas: [cofre Windows, backup seletivo e descoberta no Codex](validation-windows-codex.md). O registro distingue chamadas pelo SDK de descoberta pelo App Server; não equivale a homologação da interface desktop. Nenhuma configuração operacional foi modificada.

1. **Núcleo puro — concluído.** Domínios, checksum, comparação e guards; preservar o fluxo Windows e apresentar resultados de testes antes de prosseguir.
2. **Backends + servidor SFTP simulado — concluído.** SFTP/Paramiko e FTPS/ftplib com interface comum, fingerprint SSH confirmada e certificado TLS validado. Fixture baseada em `paramiko.ServerInterface` pronta para as etapas 3–5; acrescentado servidor FTPS local. SHA-256 remoto calculado pela leitura integral do conteúdo: erro de leitura/verificação impede sucesso. Limites de FTPS documentados.
3. **Credenciais, configuração e execução da migração — concluído.** Keyring, age e ambiente; YAML validado, JSON legado e migração de cadastros testada em cópia isolada. Senhas DPAPI permanecem intocadas e exigem recadastro; nenhum segredo é exportado em claro. A etapa 4 expõe essa lógica pela CLI.
4. **CLI — concluída.** Operações e aliases em português, cadastro interativo e `migrate`, reutilizando a lógica testada. Opt-in de publicação, prévia vinculada por hash, bloqueio de conflitos, backup verificado, verificação pós-upload e registros de falhas parciais. Nenhuma exclusão remota. Evidências e limitações acima.
5. **MCP e token de prévia — concluída.** SDK oficial com stdio, sete ferramentas e token emitido por prévia sem bloqueios, vinculado ao hash, válido por 5 minutos e obrigatório no deploy Python/MCP. Consumo único entre processos; prévia alterada bloqueia o envio. Confirmação explícita e `publish_enabled` continuam necessários. Evidências e limites acima.
6. **Testes ampliados — concluída.** Suíte de 467 testes, 94,77% de cobertura local, com evidências de concorrência, backup, interrupção, recuperação, credenciais, CLI/MCP e tokens. Limites de ambiente e recuperação documentados acima; sem alterações no código de produção nesta etapa.
7. **Empacotamento e CI — concluído.** Pyproject, Docker, pip/pipx e binários implementados. [CI final 36276055669](https://github.com/herlonventura/codex-locaweb-sftp-skill/actions/runs/36276055669) verde nos sete jobs: três sistemas, Python 3.11/3.14 e Docker. 467 testes por ambiente, cobertura entre 94,77% e 95,09%, verificações adicionais do pacote e binários. Três artefatos disponíveis; nenhum SO pendente e nenhuma issue de falha necessária. Não publicado no PyPI; limites no guia de distribuição.
8. **Documentação — concluída.** README, instalação, legado preservado, seis exemplos MCP, migração documentada e roteiro de recuperação. Sintaxe, argumentos, links e inicialização/listagem stdio conferidos. Limites e pendências explícitos acima. Nenhuma migração de produção nem configuração de aplicativo executada nesta etapa.

Cada etapa depende dos testes pertinentes passando. O repositório público deve conter apenas código, documentação e exemplos fictícios; cadastros reais, credenciais, logs e backups continuam excluídos.
