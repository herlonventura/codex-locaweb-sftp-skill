param(
    [Parameter(Mandatory=$true, Position=0)][string]$Request
)

$ErrorActionPreference = 'Stop'
$text = $Request.Trim()
$match = [regex]::Match($text, '^/mcplocaweb/(?<domain>[a-z0-9.-]+)\s*(?<action>.*)$', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
if (-not $match.Success) { throw 'Formato: /mcplocaweb/dominio.com.br [acao]' }
$domain = $match.Groups['domain'].Value.ToLowerInvariant()
$action = $match.Groups['action'].Value.Trim()
$catalog = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'config\sites.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $catalog.PSObject.Properties[$domain]) { throw "Dominio nao cadastrado: $domain" }

$command = $null
$path = $null
switch -Regex ($action) {
    '^$' { $command = 'info'; break }
    '^(enviar arquivos atualizados|publicar atualiza[cç][oõ]es)$' { $command = 'deploy'; break }
    '^(backup|baixar backup)$' { $command = 'backup'; break }
    '^(comparar|ver diferen[cç]as)$' { $command = 'diff'; break }
    '^(pr[eé]via de envio|simular envio)$' { $command = 'dry-run'; break }
    '^(testar|verificar conex[aã]o)$' { $command = 'test'; break }
    '^listar arquivos(?:\s+(.+))?$' { $command = 'files'; $path = $Matches[1]; break }
    '^baixar arquivo\s+(.+)$' { $command = 'download'; $path = $Matches[1]; break }
    default { throw "Acao nao reconhecida: $action" }
}

$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'locaweb.ps1'), $command, $domain)
if ($path) { $arguments += @('-Path', $path) }
& powershell.exe @arguments
exit $LASTEXITCODE
