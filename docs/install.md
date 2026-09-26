# Instalar e cadastrar o primeiro site

Escolha a implementação Python para uma instalação nova. Ela não depende do FileZilla, WinSCP ou de senhas já existentes no computador. Requer Python 3.11+ para pip/pipx; os [binários nativos](distribution.md) incluem o interpretador. A versão `0.1.0a2` é de desenvolvimento e não está no PyPI.

## Instalação local

Em um terminal com Git e Python:

```sh
git clone https://github.com/herlonventura/vhe-deploy-sftp-ftps-mcp.git
cd vhe-deploy-sftp-ftps-mcp
python -m venv .venv
```

Ative o ambiente conforme o sistema:

```powershell
# Windows / PowerShell
.\.venv\Scripts\Activate.ps1
```

```sh
# Linux / macOS
source .venv/bin/activate
```

Então instale e abra o cadastro:

```sh
python -m pip install .
vhe-deploy --help
vhe-deploy configurar
```

Se a ativação for bloqueada no Windows, invoque diretamente `.\.venv\Scripts\python.exe -m pip install .` e `.\.venv\Scripts\vhe-deploy.exe configurar`; não é necessário mudar a política de execução.

Alternativa com pipx já instalado: dentro do repositório, `pipx install .` e depois `vhe-deploy configurar`. Não confunda com `pipx install vhe-deploy`: sem `.`/caminho/repositório o comando busca um nome no índice, onde este projeto não foi publicado. Para fixar uma revisão, selecione o commit desejado antes de instalar. [Wheel, pipx, Docker, binários e limites](distribution.md).

## Perguntas do cadastro

O assistente pergunta domínio completo, protocolo, servidor, porta, usuário, pasta local, raiz remota e provedor da senha. Use dados do seu provedor; o domínio nem sempre é o endereço do SFTP. Escolha uma pasta local contendo somente os arquivos publicáveis, normalmente `public_html`; não cadastre a pasta que contém backups, configurações ou fontes privadas.

No SFTP, confira a fingerprint SHA256 com o provedor por um canal independente. Sem essa confirmação, deixe pendente: o programa não autentica. `scan-key DOMINIO` consulta a chave observada, mas não a valida nem a salva automaticamente. Depois da conferência:

```text
vhe-deploy set-key exemplo.com.br --fingerprint SHA256_CONFERIDA --confirm
vhe-deploy credential exemplo.com.br
```

`SHA256_CONFERIDA` é um marcador a substituir pela fingerprint completa no formato `SHA256:...`; não é uma chave utilizável. Senhas de keyring/age são pedidas em prompt oculto no terminal, nunca no chat ou nos argumentos. Em `env`, o comando informa o nome exato da variável a injetar de modo privado. [Provedores e requisitos](configuration-credentials.md).

Keyring indisponível não provoca fallback: o cadastro fica com credencial pendente. No Linux sem sessão Secret Service ou em contêiner, configure age/ambiente explicitamente. age requer executável confiável e configuração da identidade e destinatário antes de gravar senha. A mensagem de cadastro bem-sucedido pode ter `credential_status: pending`; não significa que uma conexão já foi testada.

## Conferir antes de publicar

```sh
vhe-deploy list
vhe-deploy info exemplo.com.br
vhe-deploy test exemplo.com.br
vhe-deploy compare exemplo.com.br
```

`list` e `info` leem o cadastro local. `test` autentica no servidor; `compare` lê arquivos remotos para calcular hashes. Execute os dois últimos somente no domínio que pretende acessar. O cadastro, por si só, não inicia rede nem publicação.

Por padrão, os arquivos ficam em `~/.vhe-deploy/sites.yaml` e `settings.yaml`, e os recibos/backups em `~/.vhe-deploy/state`. Mantenha a pasta privada fora do Git, fora da publicação e sem sincronização dos recibos entre máquinas. No Windows confira as ACLs herdadas; o programa não as configura.

Publicação começa desativada. Só habilite `publish_enabled: true` no cadastro do site **e** em `settings.yaml` quando tiver revisado pasta de origem, raiz remota, identidade do servidor e conteúdo publicável. FTPS também exige a declaração administrativa documentada na [CLI](cli.md#publicar-uma-prévia-revisada). Não habilite por um pedido genérico de instalação.

Para usar com uma IA, prossiga para [configuração por cliente](mcp-clients.md). Para publicação, leia o fluxo completo de prévia/token na [CLI](cli.md). Para quem já tem a skill antiga, use o [guia de migração](migration-from-codex-skill.md).

Para baixar bundles portáteis sem instalar Python, consulte [releases e checksums](releases.md).

`test DOMINIO` usa uma senha já cadastrada **neste VHE Deploy**. Senhas salvas no FileZilla ou na instalação antiga não contam como cadastro. Use `credential DOMINIO` primeiro; digite e confirme a senha no prompt oculto do terminal.
