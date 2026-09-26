# Testes e evidências de recuperação — etapa 6

Os testes usam arquivos temporários, domínios fictícios e servidores SFTP/FTPS em `127.0.0.1`, com portas dinâmicas. Chaves SSH e certificados TLS são gerados durante a execução. Nenhum teste da suíte depende dos cadastros de hospedagem do usuário ou deve ser adaptado para apontar a um site real.

Resultado em 26/09/2026: **467 testes aprovados, sem skips com age disponível; 94,77% de cobertura combinada de instruções e ramos**, em Windows/Python 3.14.3. A etapa acrescentou 24 cenários à suíte de 443 testes. O teste PowerShell legado, `pip check` e a análise de sintaxe pela gramática Python 3.11 também passaram.

## Executar

Dentro de um ambiente virtual, a partir da raiz do repositório:

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=mcp_locaweb_sftp --cov-branch --cov-report=term-missing --cov-fail-under=81
```

Para incluir criptografia age real, instale `age` e `age-keygen` de fonte confiável no mesmo diretório. Coloque `age` no PATH ou configure `MCP_LOCAWEB_TEST_AGE` com seu caminho absoluto. Sem isso, os testes age ficam **skipped**; não conte essa execução como validação criptográfica completa. O cofre nativo continua simulado para não gravar senhas no perfil do usuário.

No Windows, o teste legado é independente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test-deploy-local.ps1
```

Ele usa o transporte simulado do teste e a instalação WinSCP esperada pelo fluxo PowerShell. O código Python e seus testes não dependem de WinSCP.

## O que é verificado

| Área | Evidência |
|---|---|
| Comparação | Hashes, datas UTC, fronteira de dois segundos, nomes ambíguos, conflitos e bloqueio integral do lote |
| Transporte | SFTP/FTPS locais reais: autenticação, identidade do servidor, TLS, caminhos, leituras, substituição, truncamento e SHA-256 após escrita |
| Credenciais | Vínculo por domínio/conexão, cofre simulado, age real, adulteração, segredo omitido nos erros e ausência de fallback automático |
| Prévia/token | Vínculo de hash e domínio, fronteira de 300 segundos, mudanças de relógio, persistência do consumo e disputa entre processos |
| Backup | Todos os originais antes do primeiro envio; falha no último backup impede inclusive upload de arquivo novo que seria enviado antes |
| Arquivos locais | Mudança depois da prévia, troca entre stat/abertura, alteração por outro handle durante leitura, escrita incompleta e recusa de sobrescrever snapshot existente |
| Concorrência | Outro domínio no mesmo endpoint não publica enquanto o primeiro lote mantém a trava; depois, a prévia antiga é rejeitada |
| Cadastro | Edição concorrente rejeitada sem perder a entrada do editor que já detém a trava |
| Interrupção abrupta | Processo filho escreve conteúdo parcial via SFTP/FTPS e encerra com `os._exit`, sem executar blocos `finally` |
| Retomada | Backup original e diário sobrevivem; token continua consumido; trava residual impede nova operação até revisão explícita; comparação identifica conflito remoto |
| Cancelamento MCP | Cancelamento cooperativo do cliente não libera a trava enquanto o worker ainda pode escrever; diário final é conferido depois |
| CLI | Sucesso, erro, argumentos inválidos, conflito e falha parcial com códigos 0/1/2/3/4; hash sem token e repetição do token não publicam |
| MCP | Cliente oficial e processo stdio, descoberta das sete ferramentas, schema e resposta estruturada; segredo não aparece em erros |

Os cenários de envio, recuperação e concorrência rodam nos dois protocolos. A fixture compartilhada `site_runtime` mantém o mesmo cadastro fictício usado nos testes da aplicação e do MCP. `tests/crash_worker.py` é exclusivamente um instrumento de teste e recusa host diferente de loopback, usuário diferente do fictício ou outra raiz remota.

## Interrupção e recuperação observadas

O teste de processo interrompido confirma esta sequência:

1. O token é consumido e o original remoto é copiado/verificado localmente.
2. O diário grava a intenção de substituir `index.html`.
3. O processo filho escreve conteúdo parcial no servidor local e encerra abruptamente.
4. O processo de teste encontra `status: partial`, arquivo ativo, backup intacto e trava residual. Não existe confirmação falsa de upload concluído.
5. Outra operação é rejeitada enquanto a trava permanece. O teste confirma o encerramento do filho e remove somente a trava vazia daquele diretório temporário, simulando revisão manual do operador.
6. Uma nova prévia aponta conflito no arquivo remoto parcialmente gravado e não emite token. O original preservado continua disponível para uma decisão de recuperação.

Isso não adiciona rollback nem restauração automática. O produto continua exigindo revisão do diário e do estado remoto antes de retomar. A remoção manual da trava ocorre apenas no teste, em caminho temporário verificado.

O cancelamento cooperativo do cliente também não equivale a desfazer a publicação: o worker já iniciado pode concluir e registrar sucesso depois que a chamada do cliente foi cancelada. O diário é a referência para descobrir o resultado. Encerramento forçado do processo e cancelamento cooperativo são cenários distintos, testados separadamente.

## Limites da evidência

- A cobertura contabiliza o processo principal; a execução dos servidores/CLI em subprocessos não é agregada ao percentual. Esses fluxos têm asserções próprias sobre arquivos, protocolo e diários.
- O encerramento abrupto exercita perda do processo, não corte de energia, falha de disco físico ou garantia de recuperação de desastre.
- A disputa de trava foi testada com a mesma pasta de estado. Outras máquinas, clientes FTP independentes e aplicações remotas não são bloqueados por essa trava.
- O servidor FTPS simulado atende às precondições de confinamento e ausência de escritores externos. Isso não comprova que um provedor real as atenda.
- Na etapa 6, o teste de interoperabilidade usou o cliente oficial do SDK MCP. A [validação posterior Windows/Codex](validation-windows-codex.md) comprovou cofre real Windows e descoberta das sete ferramentas pelo Codex App Server. Interfaces e aprovações dos aplicativos continuam pendentes.
- Na entrega da etapa 6, a execução comprovada era Windows/Python 3.14.3. A etapa 7 acrescentou CI em Windows/Linux/macOS com Python 3.11 e 3.14, instalação e binários: [evidências de distribuição](distribution.md). Os números acima preservam o registro da etapa 6.
- Os novos cenários não exigiram alteração do código de produção. A etapa acrescenta evidências e reutiliza fixtures; não amplia permissões nem modifica a instalação operacional.

Os números por entrega e as dependências utilizadas estão no [registro das etapas](migration-plan.md). Cobertura maior que 80% é uma condição de validação, não demonstração de ausência de falhas.

## Retenção automática após as oito etapas

[Política de três envios por domínio](retention.md): os novos testes cobrem cinco envios reais em SFTP/FTPS com preservação de backup completo; erros, registros inválidos, outros domínios, relógio regressivo, hard links, reparse points, exclusão local interrompida e nova tentativa. Falha na limpeza ou na gravação do seu resultado não invalida o sucesso do upload já salvo.

Validação local Windows/Python 3.14.3: **489 aprovados e um skip**, cobertura de instruções/ramos **94,76%**, com age real. O skip é a criação de link simbólico sem privilégio no Windows; o teste de reparse point simulado e o de hard link real passaram. Quatro cenários adicionais de aviso/falha do diário da limpeza passaram depois; os 49 testes CLI/MCP também passaram após a atualização das descrições. Os testes apagam somente execuções fictícias em diretórios temporários; nenhuma limpeza operacional foi executada.
