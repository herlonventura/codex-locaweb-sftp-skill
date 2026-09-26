# Baixar uma versão do VHE Deploy

Use [Releases](https://github.com/herlonventura/vhe-deploy-sftp-ftps-mcp/releases), onde os pacotes ficam vinculados à versão e ao commit. A `v0.1.0a2` é uma **pré-versão**, não uma versão estável homologada em todos os ambientes.

## Qual arquivo baixar

| Ambiente | Pacote |
|---|---|
| Windows X64 | `vhe-deploy-0.1.0a2-windows-x64.zip` |
| Linux X64 | `vhe-deploy-0.1.0a2-linux-x64.tar.gz` |
| macOS ARM64 (Apple Silicon) | `vhe-deploy-0.1.0a2-macos-arm64.tar.gz` |
| Python 3.11+ / pipx | `vhe_deploy-0.1.0a2-py3-none-any.whl` |
| Código do pacote Python | `vhe_deploy-0.1.0a2.tar.gz` |

Confira `SHA256SUMS.txt`, publicado na mesma release. No PowerShell, use `Get-FileHash arquivo.zip -Algorithm SHA256`; no Linux `sha256sum arquivo.tar.gz`; no macOS `shasum -a 256 arquivo.tar.gz`. Compare com a linha correspondente. O checksum detecta divergência de conteúdo; não substitui uma assinatura independente.

Os bundles são portáteis, não instaladores MSI/EXE com assistente. Extraia o arquivo completo, mantendo as duas pastas de comandos e seus diretórios `_internal`. Abra um terminal na pasta extraída. No Windows:

```powershell
.\vhe-deploy\vhe-deploy.exe configurar
```

No Linux/macOS:

```sh
./vhe-deploy/vhe-deploy configurar
```

Configure o cliente de IA com o caminho absoluto de `vhe-deploy-mcp/vhe-deploy-mcp` (mais `.exe` no Windows). Os exemplos estão em [clientes MCP](mcp-clients.md). O pacote não cadastra sites, importa senhas ou modifica clientes de IA automaticamente. O `age` externo não está incluído; confira [credenciais](configuration-credentials.md).

Os executáveis não têm assinatura Authenticode nem notarização Apple. Outras arquiteturas, versões antigas de SO e distribuições Linux diferentes do ambiente de CI não são garantidas. Não desative proteções do sistema para executar um arquivo cuja origem você não verificou.

## Senha cadastrada não é senha testada

`credential DOMINIO` grava no cofre configurado. `test DOMINIO` tenta autenticar usando a credencial desse cadastro do VHE Deploy. Uma senha existente no FileZilla ou numa instalação antiga não é importada. Primeiro cadastre e depois teste. A mensagem de cofre inacessível também pode indicar falta de permissões ou indisponibilidade do cofre; não significa necessariamente senha incorreta no servidor.

## Conflitos de horário

Uma edição rápida pode parecer mais antiga que o arquivo remoto quando os relógios diferem. A prévia informa quais arquivos estão em conflito e não emite token nesse caso. Confira conteúdo, datas e relógios. Quando se comprovar que a versão remota corresponde ao último envio e a mudança local é a desejada, faça a edição após corrigir/aguardar a diferença e gere outra prévia. Não altere datas artificialmente nem ignore uma alteração remota legítima.

## Verificação operacional adicional

Um ensaio autorizado em hospedagem SFTP real, no Windows, verificou arquivo novo, download, alteração e restauração dos bytes baixados, com backups das substituições e leitura final por HTTPS. Os arquivos anteriores permaneceram com os mesmos hashes. Houve bloqueio por diferença de aproximadamente vinte segundos nas datas; a retomada ocorreu após revisão e espera. Essa evidência não substitui os testes de FTPS, outros provedores ou interfaces de aprovação. Nenhum cadastro, domínio de cliente, senha ou recibo privado desse ensaio integra a distribuição pública.
