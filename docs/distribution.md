# Instalação e distribuição Python

Versão inicial de desenvolvimento: `0.1.0a1`. O pacote se chama `vhe-deploy` e requer Python 3.11 ou superior. Não foi publicado no PyPI nem em registro de contêineres.

## Evidência do VHE Deploy — 26/09/2026

**Sete jobs aprovados** no [CI 36278483565](https://github.com/herlonventura/codex-locaweb-sftp-skill/actions/runs/36278483565), commit `eccc7f8aa47f092c36aa0c6411ab3e1021081869`: seis combinações de SO/Python e um job Docker. A suíte principal passou com **496 testes em cada combinação**, incluindo a retenção automática, já com a marca e os comandos VHE Deploy. Os testes da distribuição rodam separadamente para não mascarar o uso dos comandos instalados/congelados com importação das fontes.

| Plataforma do runner | Python 3.11 | Python 3.14 | Binário validado |
|---|---|---|---|
| Windows X64 | 496 aprovados; 95,02% cobertura | 496 aprovados; 94,94% cobertura | Sim, construído com Python 3.11 |
| Linux X64 | 496 aprovados; 95,23% cobertura | 496 aprovados; 95,16% cobertura | Sim, construído com Python 3.11 |
| macOS ARM64 | 496 aprovados; 95,23% cobertura | 496 aprovados; 95,16% cobertura | Sim, construído com Python 3.11 |

Em cada ambiente houve instalação por pip e pipx e teste MCP stdio. Dois cenários adicionais verificaram a distribuição instalada contra SFTP e FTPS locais; os mesmos dois cenários foram repetidos com os binários em cada SO. O job Docker passou nas verificações descritas adiante. Windows local/Python 3.14.3 também passou no pacote instalado com os novos comandos; os binários desta marca foram validados pelo CI. Os avisos em Python 3.11 referem-se a APIs legadas usadas pelo servidor FTPS de teste, não a falhas da suíte.

Os artefatos **distribution-Windows-X64**, **distribution-Linux-X64** e **distribution-macOS-ARM64** estão na [execução atual](https://github.com/herlonventura/codex-locaweb-sftp-skill/actions/runs/36278483565), contendo wheel `vhe_deploy`, fonte e bundle nativo com os dois novos comandos. O download dos artefatos do GitHub pode exigir login. Os três sistemas passaram; não foi necessário abrir issue para plataforma pendente. WSL e outras arquiteturas/versões de SO não foram testados. As interfaces dos aplicativos MCP continuam sem homologação; a descoberta no Codex App Server foi validada separadamente. Nenhum segredo real, cadastro de cliente ou conta de hospedagem foi usado.

## pip e pipx

Em uma cópia do repositório, instale em ambiente virtual:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install .
vhe-deploy --help
vhe-deploy configurar
```

Ou use o pipx previamente instalado:

```sh
pipx install .
vhe-deploy configurar
```

Também é possível instalar o arquivo `.whl` dos artefatos do workflow **Python distribution** com `pip install caminho/do/pacote.whl` ou `pipx install caminho/do/pacote.whl`. Não use `pip install vhe-deploy` sem indicar arquivo/repositório: este projeto ainda não está publicado no PyPI. Para versões reproduzíveis do código, use um commit específico do repositório; os intervalos de dependências não constituem um lockfile.

Os comandos instalados são `vhe-deploy` (CLI) e `vhe-deploy-mcp` (servidor stdio). O módulo `python -m vhe_deploy` continua funcionando. Configuração e estado ficam, por padrão, em `~/.vhe-deploy`, fora do pacote. A instalação não importa cadastros, cadastra senhas nem conecta a servidores.

Para iniciar o MCP, configure o cliente com o caminho absoluto de `vhe-deploy-mcp`. As opções `--sites`, `--settings` e `--state-dir` permitem indicar arquivos/diretório privados. Não coloque senhas na configuração do cliente. Veja o [contrato MCP](mcp-setup.md) e a [CLI](cli.md).

## Binários por sistema

O CI compila com PyInstaller **no próprio sistema de destino**. O arquivo `native-<SO>-<arquitetura>` contém duas pastas, uma por comando: ZIP no Windows e TAR.GZ em Linux/macOS, preservando permissões e links do bundle. Extraia o arquivo inteiro e preserve as pastas `_internal`; copiar somente o executável não funciona. No Windows os comandos terminam em `.exe`. No Linux/macOS use `tar -xzf native-<SO>-<arquitetura>.tar.gz`.

O binário inclui o Python e as bibliotecas Python, mas não inclui cadastros, credenciais ou o executável externo `age`. Para usar esse provedor, instale `age` e configure seu caminho conforme [credenciais](configuration-credentials.md). Cofre nativo requer serviços e permissões do SO. O CI usa cofres simulados; uma [validação posterior do pacote instalado no Windows](validation-windows-codex.md) usou o cofre real com credencial fictícia. Isso não comprova o cofre dos binários nem a disponibilidade em outras máquinas.

Os executáveis são de desenvolvimento, sem assinatura Authenticode ou notarização Apple. O artefato corresponde ao runner/arquitetura identificados pelo CI; não promete compatibilidade com versões antigas do SO, outras arquiteturas ou toda distribuição Linux. Os arquivos ficam nos artefatos de uma execução bem-sucedida por 14 dias; não são uma release permanente.

## Docker

```sh
docker build -t vhe-deploy:local .
docker run --rm --entrypoint vhe-deploy vhe-deploy:local --help
```

A entrada padrão é MCP stdio. O contêiner roda como UID 10001, inclui certificados CA e `age` da distribuição Debian, e não abre uma porta HTTP. Para integrar com um cliente, use `docker` como comando e argumentos equivalentes a:

```sh
docker run --rm -i \
  --mount type=bind,src=/caminho/privado/config,dst=/config,readonly \
  --mount type=bind,src=/caminho/privado/estado,dst=/state \
  --mount type=bind,src=/caminho/do/site,dst=/site-local,readonly \
  vhe-deploy:local \
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
