# Instalação e distribuição Python

Versão inicial de desenvolvimento: `0.1.0a1`. O pacote se chama `mcp-locaweb-sftp` e requer Python 3.11 ou superior. Não foi publicado no PyPI nem em registro de contêineres. A validação da etapa 7 está em andamento; os resultados serão registrados aqui depois da execução do CI.

## pip e pipx

Em uma cópia do repositório, instale em ambiente virtual:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install .
mcp-locaweb-sftp --help
mcp-locaweb-sftp configurar
```

Ou use o pipx previamente instalado:

```sh
pipx install .
mcp-locaweb-sftp configurar
```

Também é possível instalar o arquivo `.whl` dos artefatos do workflow **Python distribution** com `pip install caminho/do/pacote.whl` ou `pipx install caminho/do/pacote.whl`. Não use `pip install mcp-locaweb-sftp` sem indicar arquivo/repositório: este projeto ainda não está publicado no PyPI. Para versões reproduzíveis do código, use um commit específico do repositório; os intervalos de dependências não constituem um lockfile.

Os comandos instalados são `mcp-locaweb-sftp` (CLI) e `mcp-locaweb-sftp-mcp` (servidor stdio). O módulo `python -m mcp_locaweb_sftp` continua funcionando. Configuração e estado ficam, por padrão, em `~/.mcp-locaweb-sftp`, fora do pacote. A instalação não importa cadastros, cadastra senhas nem conecta a servidores.

Para iniciar o MCP, configure o cliente com o caminho absoluto de `mcp-locaweb-sftp-mcp`. As opções `--sites`, `--settings` e `--state-dir` permitem indicar arquivos/diretório privados. Não coloque senhas na configuração do cliente. Veja o [contrato MCP](mcp-setup.md) e a [CLI](cli.md).

## Binários por sistema

O CI compila com PyInstaller **no próprio sistema de destino**. O ZIP `native-<SO>-<arquitetura>.zip` contém duas pastas, uma por comando. Extraia o ZIP inteiro e preserve as pastas `_internal`; copiar somente o executável não funciona. No Windows os comandos terminam em `.exe`. No Linux/macOS pode ser necessário restaurar permissão de execução após extrair: `chmod +x caminho/do/executavel`.

O binário inclui o Python e as bibliotecas Python, mas não inclui cadastros, credenciais ou o executável externo `age`. Para usar esse provedor, instale `age` e configure seu caminho conforme [credenciais](configuration-credentials.md). Cofre nativo requer serviços e permissões do SO; os testes de cofre são simulados e não comprovam disponibilidade no computador do usuário.

Os executáveis são de desenvolvimento, sem assinatura Authenticode ou notarização Apple. O artefato corresponde ao runner/arquitetura identificados pelo CI; não promete compatibilidade com versões antigas do SO, outras arquiteturas ou toda distribuição Linux. Os ZIPs ficam nos artefatos de uma execução bem-sucedida por 14 dias; não são uma release permanente.

## Docker

```sh
docker build -t mcp-locaweb-sftp:local .
docker run --rm --entrypoint mcp-locaweb-sftp mcp-locaweb-sftp:local --help
```

A entrada padrão é MCP stdio. O contêiner roda como UID 10001, inclui certificados CA e `age` da distribuição Debian, e não abre uma porta HTTP. Para integrar com um cliente, use `docker` como comando e argumentos equivalentes a:

```sh
docker run --rm -i \
  --mount type=bind,src=/caminho/privado/config,dst=/config,readonly \
  --mount type=bind,src=/caminho/privado/estado,dst=/state \
  --mount type=bind,src=/caminho/do/site,dst=/site-local,readonly \
  mcp-locaweb-sftp:local \
  --sites /config/sites.yaml --settings /config/settings.yaml --state-dir /state
```

Substitua os caminhos e prepare permissões: UID 10001 precisa ler configuração/site e gravar em `/state`. Configure `local_root: /site-local` dentro do YAML do contêiner. Use `-i`, sem `-t`, para preservar stdio. O estado deve persistir entre chamadas: contém backups, diários e tokens; não publique usando estado descartável. Configuração montada somente para leitura permite operar sites existentes, mas não cadastrá-los; faça o cadastro localmente ou conceda escrita deliberadamente ao diretório privado.

Escolha explicitamente age ou ambiente em instalações sem cofre nativo disponível. A chave privada age deve ser montada separadamente, com acesso restrito, e os caminhos de age/identidade devem referir-se ao contêiner. Não inclua segredos no Dockerfile, na imagem, no Git ou em argumentos. Variáveis de ambiente são suportadas, mas são acessíveis aos processos/administradores autorizados; não são equivalentes a um cofre.

A imagem usa Python 3.11 em Debian Bookworm e dependências resolvidas no build; não há promessa de imagem reproduzível por digest. Não publica nada automaticamente. O teste Docker verifica usuário não-root, CLI, age e inicialização/descoberta/rejeição de pedido inválido via MCP; operações SFTP/FTPS completas são verificadas fora do contêiner.

## Verificações de distribuição

```sh
python -m pip install -r requirements-dev.txt -r requirements-build.txt
python -m build
python packaging/check_archives.py
python -m twine check dist/*
python packaging/check_install.py
python packaging/build_binaries.py
python packaging/check_install.py --frozen
```

O wheel é reconstruído a partir do sdist. A conferência permite somente fontes Python e metadados explícitos nos arquivos. O teste de instalação usa venv e pipx isolados em `build/`, executa os comandos fora da árvore de fontes e testa prévia na CLI → envio MCP → verificação/backup → rejeição de token reutilizado em servidores locais SFTP e FTPS. O mesmo fluxo é repetido com os binários.

O workflow tenta Windows, Linux e macOS, com Python 3.11 e 3.14. A suíte exige cobertura combinada mínima de 81%; binários são gerados em Python 3.11. Só publica artefatos do job depois dos testes correspondentes passarem. Não envia pacotes ao PyPI, imagens a registros ou arquivos a hospedagens. O teste PowerShell legado depende do WinSCP e não integra esta matriz Python.

O repositório ainda não declara uma licença de redistribuição própria; a publicação pública e o empacotamento não substituem essa definição pelo autor. Licenças das dependências permanecem aplicáveis.
