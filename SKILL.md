---
name: mcplocaweb
description: Gerencia sites cadastrados via SFTP/FTPS com consulta, comparação, backup e publicação controlada quando o usuário seleciona um domínio completo.
---

# Sites via SFTP/FTPS

Leia `README.md` para instalação e configuração quando o ambiente ainda não estiver preparado. O catálogo privado `config/sites.json` associa cada domínio completo à sua origem local e ao diretório remoto. Nunca deduza um domínio por apelido ou grafia aproximada. Credenciais ficam fora do repositório; não as mostre nem as copie para arquivos de resposta.

O texto `/mcplocaweb/<dominio>` consulta apenas o cadastro. É uma convenção textual desta skill, não um comando de barra nativo do Codex. A invocação nativa é `$mcplocaweb`.

Comandos canônicos:

- `/mcplocaweb/<dominio> testar`: testa conexão e diretório remoto.
- `/mcplocaweb/<dominio> backup`: baixa um snapshot do diretório remoto.
- `/mcplocaweb/<dominio> comparar`: compara os arquivos locais e remotos por checksum.
- `/mcplocaweb/<dominio> previa de envio`: mostra diferenças, conflitos e arquivos bloqueados.
- `/mcplocaweb/<dominio> enviar arquivos atualizados`: faz backup dos arquivos que serão substituídos, envia apenas arquivos novos ou diferentes e verifica o hash remoto. Não exclui arquivos.

Aceite também um pedido claro em linguagem natural, como “envie os arquivos atualizados”, quando um único domínio estiver identificado sem ambiguidade. Perguntas sobre como enviar não autorizam uma publicação. Se o domínio estiver ausente ou ambíguo, peça o domínio completo antes de qualquer operação remota.

Execute o comando com o wrapper `scripts/mcplocaweb.ps1`, resolvido a partir da pasta desta skill. Exemplo em PowerShell: `& '<caminho-da-skill>\scripts\mcplocaweb.ps1' '/mcplocaweb/exemplo.com.br comparar'`. O wrapper chama Windows PowerShell 5.1. A origem local vem somente do catálogo; não aceite um caminho alternativo na mensagem.

Antes de publicar, confira o resultado da comparação ou da prévia. Se houver conflito de versão, arquivo sensível bloqueado ou falha de backup, não force o envio. Relate com precisão qualquer envio parcial e não o repita automaticamente. Não altere DNS, contas, banco de dados ou outros domínios sem um pedido próprio.
