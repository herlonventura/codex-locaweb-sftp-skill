# Retenção automática: três envios concluídos por site

Na implementação Python, CLI e MCP mantêm automaticamente **os três últimos envios concluídos com sucesso de cada domínio**, incluindo o envio recém-concluído. A limpeza ocorre somente depois de todos os uploads serem verificados e o registro de sucesso ser salvo. Não exige outra confirmação além da autorização do envio.

Ao concluir o quarto envio, a pasta da execução mais antiga elegível é removida: backup dos arquivos substituídos, snapshot das versões enviadas e diário daquela execução. Não são três versões de cada arquivo; são três **execuções de envio**. O backup automático continua limitado aos arquivos substituídos, não ao site inteiro.

São preservados:

- Backups completos solicitados pelo comando `backup` / ferramenta `backup_site`.
- Execuções com erro, parciais ou interrompidas, inclusive seus diários de recuperação.
- Pastas sem registro válido de envio concluído, arquivos inesperados e outros domínios.
- A execução que acabou de concluir, mesmo após regressão do relógio do computador.

Uma comparação, prévia, envio sem alterações ou envio que falhou **não dispara limpeza**. Backups completos e falhas podem continuar acumulando espaço; não fazem parte da retenção dos três envios.

## Segurança e resultado

A limpeza é exclusivamente local, em `state/runs/DOMINIO/ID/`. Não apaga arquivos da hospedagem. Antes de remover uma execução, confere domínio, caminho absoluto, nome gerado, registro de sucesso e toda a árvore. Não segue links simbólicos, junções/reparse points nem remove árvores com hard links ou arquivos especiais. Não usa exclusão recursiva irrestrita.

Os novos registros têm `completed_at_ns` para ordenar conclusões; registros anteriores válidos usam a data de modificação do diário. A limpeza tem trava por domínio além da trava do envio. Uma trava residual de limpeza preserva as cópias e exige revisão manual; não deve ser apagada enquanto outro processo pode estar limpando.

`data.retention` informa o limite, IDs removidos e quantidade de erros/itens inseguros ignorados. Se a limpeza não puder terminar, o envio já verificado continua com `status: success` e uma mensagem informa o problema. Não reenvie arquivos por causa desse aviso. Uma interrupção da limpeza pode remover parte de uma cópia antiga; o diário é removido por último para permitir nova tentativa posterior. As três execuções retidas não são alvo dessa remoção.

As pastas privadas devem ser protegidas contra outros processos que alterem seus arquivos: verificações de caminhos não são proteção absoluta contra adulteração concorrente por um usuário local autorizado. Não há rollback ou restauração automática.

Esta alteração não limpa instalações antigas imediatamente nem altera os scripts PowerShell legados. A regra começa no próximo envio bem-sucedido executado com a versão Python atualizada. Consulte [registros da CLI](cli.md#registros-e-recuperação) e [recuperação](recovery.md).
