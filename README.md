# Skill Codex para sites Locaweb via SFTP

Uma skill para consultar, comparar, copiar e publicar arquivos de sites hospedados na Locaweb usando um **domínio completo** como identificador. O mesmo mecanismo serve para outros provedores SFTP/FTPS. O texto `/mcplocaweb/dominio.com.br` é uma convenção entendida pela skill; **não é um comando de barra nativo do Codex nem um servidor MCP**.

O projeto inclui uma skill Codex (`SKILL.md`) e dois scripts PowerShell (`scripts/`). A skill interpreta o pedido; o wrapper traduz a ação para o backend; o backend usa a biblioteca .NET do WinSCP para acessar o servidor. Cada domínio é cadastrado localmente com uma pasta de origem e uma raiz remota. A senha fica criptografada pelo DPAPI do Windows, fora deste repositório.

## Requisitos

- Windows com Windows PowerShell 5.1;
- WinSCP instalado com `WinSCP.exe` e `WinSCPnet.dll` no mesmo diretório;
- uma conta SFTP com acesso ao site;
- um diretório local completo e identificado para cada site;
- servidor com suporte a checksum SHA-256 para a comparação e a verificação de envios.

O fluxo foi desenvolvido e testado com SFTP. O backend também aceita FTPS e FTP, mas esses protocolos exigem avaliação específica do ambiente; prefira SFTP. FTP sem criptografia só é permitido se o cadastro o habilitar explicitamente.

## Instalação

Clone o repositório na pasta de skills do Codex. Por exemplo, no PowerShell:

```powershell
git clone https://github.com/hmvimports/codex-locaweb-sftp-skill.git "$env:USERPROFILE\.codex\skills\mcplocaweb"
Set-Location "$env:USERPROFILE\.codex\skills\mcplocaweb"
Copy-Item .\config\sites.example.json .\config\sites.json
Copy-Item .\config\settings.example.json .\config\settings.json
```

Edite `config/settings.json` para apontar `winscpDirectory` ao diretório real do WinSCP. Edite `config/sites.json` com os domínios, hosts, usuários e caminhos **do seu ambiente**. Os arquivos reais de configuração são ignorados pelo Git; não remova essas regras. Reinicie o Codex se a skill não aparecer imediatamente na lista.

O exemplo contém valores fictícios. Substitua todos antes de conectar. O nome da propriedade de topo em `sites.json` deve ser o domínio completo; `localRoot` é a pasta a enviar, e `remoteRoot` é a pasta remota correspondente. Não inclua senha no JSON.

### Credencial e chave SSH

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\locaweb.ps1 credential exemplo.com.br
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\locaweb.ps1 scan-key exemplo.com.br
```

`credential` pede a senha sem a exibir e a salva por DPAPI em `%LOCALAPPDATA%\Codex-Locaweb-SFTP\credentials`. Essa credencial só pode ser decifrada pelo mesmo usuário Windows. `scan-key` mostra a chave SSH observada, mas **não a confia automaticamente**: confira a impressão digital com o provedor por um canal independente e então coloque a chave confirmada em `sites.json` ou use:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\locaweb.ps1 set-key exemplo.com.br -SshHostKeyFingerprint 'ssh-ed25519 256 SHA256:CHAVE_CONFIRMADA'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\locaweb.ps1 test exemplo.com.br
```

Se o domínio ainda não estiver no JSON, é possível cadastrá-lo com `register`; veja `help` para os parâmetros. O cadastro criado assim não contém senha e fica pendente da chave SSH confirmada.

## Como usar no Codex

Selecione `$mcplocaweb` ou escreva, por exemplo:

```text
/mcplocaweb/exemplo.com.br
/mcplocaweb/exemplo.com.br testar
/mcplocaweb/exemplo.com.br backup
/mcplocaweb/exemplo.com.br comparar
/mcplocaweb/exemplo.com.br previa de envio
/mcplocaweb/exemplo.com.br enviar arquivos atualizados
```

O comando sem ação apenas consulta o cadastro. Pedidos claros em linguagem natural, como “envie os arquivos atualizados para o servidor”, também podem acionar o envio quando um único domínio estiver selecionado. Uma pergunta como “como envio?” não aciona publicação. A skill usa sempre o domínio completo cadastrado, sem adivinhar nomes parecidos.

Para executar diretamente no terminal:

```powershell
& .\scripts\mcplocaweb.ps1 '/mcplocaweb/exemplo.com.br previa de envio'
```

Se a política de execução do PowerShell bloquear o wrapper, use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\mcplocaweb.ps1 '/mcplocaweb/exemplo.com.br previa de envio'`.

## Publicação: etapas e limites

1. `comparar` usa checksum no servidor para identificar arquivos iguais, alterados, novos apenas no computador e novos apenas no servidor.
2. Se um arquivo diferente no servidor tiver data igual ou mais recente que a cópia local (com tolerância de dois segundos), ele é considerado conflito e o envio é bloqueado.
3. A prévia lista o que seria enviado e quais caminhos sensíveis foram bloqueados. O script impede o envio de `.env`, chaves, backups, `wp-config.php` e outros nomes configurados em `Test-PublishablePath`.
4. `enviar arquivos atualizados` só funciona quando `publishEnabled` em `config/settings.json` estiver `true`. Antes do primeiro upload, os arquivos remotos que seriam substituídos são copiados localmente e seus hashes conferidos.
5. O script confere se os arquivos remotos não mudaram desde a comparação, envia somente os novos ou diferentes e verifica o SHA-256 remoto após cada envio.
6. Arquivos que só existem no servidor permanecem lá. **Nenhuma exclusão remota é feita.** Se ocorrer falha depois de parte dos uploads, `deploy-result.json` registra o que já foi enviado; examine o resultado antes de tentar novamente.

Os backups e downloads ficam em `backups/` e `sites/`; registros operacionais sem senha ficam em `logs/`. Essas pastas são ignoradas pelo Git, mas podem conter conteúdo privado do site e devem ser protegidas no computador. Não trate a comparação e o backup como substitutos de um processo completo de homologação ou recuperação de desastre.

Para habilitar publicação, altere `publishEnabled` para `true` **somente depois** de testar conexão, pasta local, raiz remota, comparação e backup do domínio. Uma publicação é uma alteração real no servidor.

## Segurança e dados não publicados

O repositório contém somente código, documentação e configurações fictícias. Não inclua `config/sites.json`, `config/settings.json`, arquivos `.dpapi`, backups, downloads, logs, exportações do FileZilla, senhas, tokens ou conteúdo de sites em commits. O `.gitignore` ajuda, mas não substitui a revisão de `git diff --cached` antes do envio.

O script não extrai senhas do FileZilla. Cadastre a credencial pelo prompt seguro `credential`. A impressão digital SSH deve ser verificada de forma independente; aceitar a chave observada sem conferência deixa a conexão vulnerável a um servidor impostor.

## Verificação local

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\locaweb.ps1 help
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\locaweb.ps1 list
```

`help` não conecta ao servidor; `list` lê apenas o catálogo local. Testes com conexão e publicação só devem usar um domínio que você administra. O backend retorna JSON para que o Codex possa relatar sucesso, conflito e falha parcial com precisão.

Há também um teste local com servidor simulado, sem conexão à hospedagem. Ele usa a biblioteca WinSCP instalada e um diretório temporário para verificar backup anterior, envio de dois arquivos, conferência de hashes, ausência de exclusões e bloqueio quando o remoto muda:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test-deploy-local.ps1
```
