# Changelog

## 0.1.0a2

- Repositório renomeado para `vhe-deploy-sftp-ftps-mcp`; CLI continua `vhe-deploy` e MCP `vhe-deploy-mcp`.
- Licença MIT e metadados de busca; documentação inicial também em inglês.
- Cadastro e erro de credenciais distinguem o cofre do VHE Deploy de senhas salvas em outros programas.
- Comparação/prévia explicam conflitos de data e possíveis diferenças de relógio, mantendo os bloqueios e a tolerância existente.
- Guia de downloads portáteis, checksums e limites da distribuição.

## 0.1.0a1

- CLI e MCP SFTP/FTPS, cofres separados dos cadastros e publicação desativada por padrão.
- Prévia com hash e token efêmero, backup dos arquivos substituídos e verificação SHA-256.
- Retenção dos três últimos envios bem-sucedidos por domínio.
- Matriz de testes Windows, Linux e macOS; pacotes Python, binários e Docker.
