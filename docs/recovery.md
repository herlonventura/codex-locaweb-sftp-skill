# Recuperar uma operação interrompida

O sistema não oferece restauração ou rollback automático. Este procedimento orienta a inspeção; não autoriza alteração no servidor. Backup de arquivos não inclui banco de dados nem equivale a snapshot simultâneo da hospedagem.

## Descobrir o resultado

1. Identifique o domínio, a pasta de estado usada pela chamada e `run_directory`, se recebido. O padrão é `~/.mcp-locaweb-sftp/state/runs/DOMINIO/ID/`.
2. Consulte `deploy-result.json`: status, fase, arquivo ativo e lista `uploaded`. Esta lista contém apenas uploads cuja leitura posterior confirmou SHA-256. Arquivo ausente da lista pode ter sido escrito parcialmente ou concluído sem registro final.
3. Preserve `backup/files/`, `backup/backup-manifest.json`, `sources/` e o diário. Não os edite para fazer o registro aparentar sucesso. O backup do deploy cobre somente os arquivos que seriam substituídos; o comando de backup completo exige seu próprio `backup-result.json` com sucesso.
4. Verifique se o processo ainda está executando. Timeout/cancelamento do cliente não prova que parou: o worker MCP pode concluir a operação e gravar o resultado depois.
5. Se houver trava residual, não a remova enquanto um processo puder escrever. Depois de confirmar seu encerramento e identificar a trava exata desta operação, um operador pode remover apenas essa trava; não limpe toda a pasta de estado nem os recibos de token.

## Decidir a próxima ação

| Situação | Próximo passo |
|---|---|
| Erro de autenticação/configuração antes de upload | Corrigir a causa, testar de forma autorizada e gerar nova prévia |
| Conflito remoto/local | Conferir qual versão deve prevalecer; o programa não tem `--force` para ignorar conflito |
| Token vencido ou consumido | Não editar o banco ou relógio; gerar e revisar outra prévia |
| `partial` ou processo morto após intenção de escrita | Comparar diário, servidor, snapshot e backup antes de qualquer novo envio |
| Conteúdo remoto incompleto | Escolher entre restaurar a versão anterior verificada ou publicar a versão correta, com autorização e conferência separadas |

`partial` é conservador: inclui resultado remoto incerto. Não significa que todos os arquivos falharam. Preserve a versão local desejada e não altere datas apenas para vencer a trava de conflito. Gere uma comparação depois de cessarem os escritores concorrentes; ela lê o servidor, não o modifica.

Uma restauração manual deve usar o arquivo anterior correto do backup, verificar seu hash contra o manifesto e conferir o conteúdo remoto após a operação. O produto não fornece um comando de restauração; não apresente uma repetição de `deploy` como se fosse rollback. Não exclua arquivos remotos para contornar as verificações.

## Evitar repetição automática

O token é de uso único e pode ter sido consumido antes da falha. Sua validade é de cinco minutos; preparação longa pode expirar antes do primeiro upload. Uma nova prévia deve ser revisada e autorizada novamente quando o plano mudou. Alterar timeout de rede/cliente não amplia o prazo do token.

A trava local coordena apenas processos que usam a mesma pasta de estado. Outras máquinas, aplicações remotas, FileZilla e o legado PowerShell podem escrever ao mesmo tempo; organize a operação para evitar isso. Não sincronize o banco de recibos entre máquinas nem use exclusão por idade para desbloquear automaticamente.

[Evidências dos testes de interrupção e retomada](testing.md) · [Detalhes da CLI](cli.md) · [Limites SFTP/FTPS](backends.md).
