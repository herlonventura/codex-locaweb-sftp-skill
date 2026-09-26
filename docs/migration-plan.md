# Migração para Python e MCP — plano e etapas 1–6

## Estado

Etapas 1–6 concluídas em 26/09/2026. As etapas 7–8 estão pendentes. Os scripts Windows existentes e a skill operacional continuam usando seu fluxo original. O código Python contém núcleo, transportes, configuração, provedores de credenciais, migração, CLI e servidor MCP real via stdio com SDK oficial. Não há pacote publicado no PyPI. Os números nas seções de cada etapa são evidências históricas daquela entrega.

O alvo é Python 3.11+. A execução local foi verificada em Windows com Python 3.14.3. Usar somente funções da biblioteca padrão no núcleo evita dependências de WinSCP, DPAPI e comandos de sistema, mas não comprova por si só execução em Linux/macOS; essa verificação pertence à matriz de testes futura.

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

## Sequência aceita

1. **Núcleo puro — concluído.** Domínios, checksum, comparação e guards; preservar o fluxo Windows e apresentar resultados de testes antes de prosseguir.
2. **Backends + servidor SFTP simulado — concluído.** SFTP/Paramiko e FTPS/ftplib com interface comum, fingerprint SSH confirmada e certificado TLS validado. Fixture baseada em `paramiko.ServerInterface` pronta para as etapas 3–5; acrescentado servidor FTPS local. SHA-256 remoto calculado pela leitura integral do conteúdo: erro de leitura/verificação impede sucesso. Limites de FTPS documentados.
3. **Credenciais, configuração e execução da migração — concluído.** Keyring, age e ambiente; YAML validado, JSON legado e migração de cadastros testada em cópia isolada. Senhas DPAPI permanecem intocadas e exigem recadastro; nenhum segredo é exportado em claro. A etapa 4 expõe essa lógica pela CLI.
4. **CLI — concluída.** Operações e aliases em português, cadastro interativo e `migrate`, reutilizando a lógica testada. Opt-in de publicação, prévia vinculada por hash, bloqueio de conflitos, backup verificado, verificação pós-upload e registros de falhas parciais. Nenhuma exclusão remota. Evidências e limitações acima.
5. **MCP e token de prévia — concluída.** SDK oficial com stdio, sete ferramentas e token emitido por prévia sem bloqueios, vinculado ao hash, válido por 5 minutos e obrigatório no deploy Python/MCP. Consumo único entre processos; prévia alterada bloqueia o envio. Confirmação explícita e `publish_enabled` continuam necessários. Evidências e limites acima.
6. **Testes ampliados — concluída.** Suíte de 467 testes, 94,77% de cobertura local, com evidências de concorrência, backup, interrupção, recuperação, credenciais, CLI/MCP e tokens. Limites de ambiente e recuperação documentados acima; sem alterações no código de produção nesta etapa.
7. **Empacotamento e CI.** Preparar pyproject, Docker, pip/pipx e binários por sistema. A matriz deve tentar Linux, macOS e Windows. A etapa pode avançar com **CI verde em pelo menos 2 sistemas**; se o terceiro falhar, abrir issue com evidências e indicar claramente que ele continua pendente. Não afirmar disponibilidade no PyPI nem suporte executado que não tenha sido confirmado.
8. **Documentação.** README honesto, configuração MCP para os clientes e **somente documentação da migração**, cuja implementação e execução de teste já pertencem à etapa 3. Registrar plataformas comprovadas, limitações, requisitos de credenciais e recuperação de falha parcial. Não repetir ou executar a migração de produção nesta etapa.

Cada etapa depende dos testes pertinentes passando. O repositório público deve conter apenas código, documentação e exemplos fictícios; cadastros reais, credenciais, logs e backups continuam excluídos.
