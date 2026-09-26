# Migração para Python e MCP — plano e etapas 1–3

## Estado

Etapas 1–3 implementadas em 26/09/2026. As etapas 4–8 estão pendentes. Os scripts Windows existentes e a skill operacional continuam usando seu fluxo original. O pacote Python atual contém núcleo, transportes, configuração, provedores de credenciais e migração de cadastros; não é um aplicativo instalável pelo PyPI nem um servidor MCP.

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

O backend ainda não substitui arquivos: essa operação exige a coordenação de backup e publicação da etapa 4. Em FTPS, a checagem prévia de existência não é atômica, e alguns servidores ocultam links; não tratar essa camada como deploy de produção pronto. [Contrato, evidências e limitações](backends.md).

## Etapa 3: configuração, credenciais e migração

Implementados modelos Pydantic imutáveis, carregamento restrito de YAML/JSON legado e seleção explícita entre cofre nativo, age e ambiente. As credenciais são vinculadas ao domínio e à identidade da conexão, retornam mascaradas e não entram no YAML. Não há fallback para arquivo em claro nem descoberta genérica de plugins de cofre.

`migrate_legacy` converte os cadastros para um destino novo, preserva a origem, aplica mapeamento explícito de pastas locais e grava publicação desativada. O relatório final indica senhas pendentes de recadastro e fingerprints ausentes. A migração não lê/descriptografa DPAPI; o recadastro é a estratégia adotada nesta implementação. Em erro de gravação, preserva o destino incompleto e não retorna sucesso. A etapa 4 exporá essa lógica na CLI; a etapa 8 documentará o fluxo final de uso.

Validação local: **330 testes aprovados, 96,78% de cobertura de instruções e ramos do código Python atual**. Testes com dados fictícios incluem migração isolada seguida de recadastro/conexão SFTP, criptografia age real, FTPS com senha age, adulteração, erro de disco, rejeição de configuração ambígua e proteção das mensagens de erro. O teste PowerShell legado passou. Windows/Python 3.14.3, Pydantic 2.13.5, PyYAML 6.0.3, keyring 25.7.0 e age 1.3.2. Sintaxe conferida com gramática de Python 3.11; execução nesse interpretador e em outros SOs continua pendente.

As operações do cofre nativo foram simuladas; nenhuma senha foi cadastrada no cofre real do usuário. age foi executado de fato com chaves temporárias. SOPS não está implementado; age atende à opção criptografada desta etapa. [Contrato e limitações](configuration-credentials.md).

## Sequência aceita

1. **Núcleo puro — concluído.** Domínios, checksum, comparação e guards; preservar o fluxo Windows e apresentar resultados de testes antes de prosseguir.
2. **Backends + servidor SFTP simulado — concluído.** SFTP/Paramiko e FTPS/ftplib com interface comum, fingerprint SSH confirmada e certificado TLS validado. Fixture baseada em `paramiko.ServerInterface` pronta para as etapas 3–5; acrescentado servidor FTPS local. SHA-256 remoto calculado pela leitura integral do conteúdo: erro de leitura/verificação impede sucesso. Limites de FTPS documentados.
3. **Credenciais, configuração e execução da migração — concluído.** Keyring, age e ambiente; YAML validado, JSON legado e migração de cadastros testada em cópia isolada. Senhas DPAPI permanecem intocadas e exigem recadastro; nenhum segredo é exportado em claro. A etapa 4 expõe essa lógica pela CLI.
4. **CLI.** Expor operações e aliases em português, inclusive `migrate`, reutilizando a lógica já testada. Exigir opt-in de publicação; parar diante de conflitos, falhas de backup ou hash divergente; registrar falhas parciais e nunca excluir arquivos remotos.
5. **MCP e token de prévia.** Usar SDK oficial com `stdio`. A trava definida é um **token efêmero gerado por `preview`, vinculado ao hash da prévia, válido por 5 minutos e exigido em `deploy`**. Vincular a prévia ao domínio, origem, destino e evidências dos arquivos. Consumir o token uma vez; expiração, reutilização ou qualquer alteração da prévia deve exigir nova prévia. A confirmação explícita do usuário e `publish_enabled` permanecem necessárias: o token comprova a prévia autorizada tecnicamente, mas, sozinho, não comprova consentimento humano.
6. **Testes ampliados.** Complementar os testes já presentes desde a etapa 1 e o servidor simulado da etapa 2. Cobrir concorrência, backups, falha parcial, credenciais, CLI/MCP e expiração/vínculo/reutilização do token. Exigir cobertura maior que 80% e apresentar limitações verificadas.
7. **Empacotamento e CI.** Preparar pyproject, Docker, pip/pipx e binários por sistema. A matriz deve tentar Linux, macOS e Windows. A etapa pode avançar com **CI verde em pelo menos 2 sistemas**; se o terceiro falhar, abrir issue com evidências e indicar claramente que ele continua pendente. Não afirmar disponibilidade no PyPI nem suporte executado que não tenha sido confirmado.
8. **Documentação.** README honesto, configuração MCP para os clientes e **somente documentação da migração**, cuja implementação e execução de teste já pertencem à etapa 3. Registrar plataformas comprovadas, limitações, requisitos de credenciais e recuperação de falha parcial. Não repetir ou executar a migração de produção nesta etapa.

Cada etapa depende dos testes pertinentes passando. O repositório público deve conter apenas código, documentação e exemplos fictícios; cadastros reais, credenciais, logs e backups continuam excluídos.
