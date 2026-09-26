$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot) 'scripts\locaweb.ps1') help | Out-Null
$settings = Get-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot) 'config\settings.json') -Raw | ConvertFrom-Json
Add-Type -Path (Join-Path $settings.winscpDirectory 'WinSCPnet.dll')

$testRoot = Join-Path $env:TEMP ('locaweb-test-' + [guid]::NewGuid().ToString('N'))
$resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
$resolvedTest = [IO.Path]::GetFullPath($testRoot)
if (-not $resolvedTest.StartsWith($resolvedTemp + '\locaweb-test-', [StringComparison]::OrdinalIgnoreCase)) { throw 'Diretorio de teste fora de TEMP.' }

function Write-Operation([string]$Action, [string]$Domain, [string]$Status, [string]$Detail) {}
function Compare-SiteFiles([string]$Domain, $Config) {
    return [ordered]@{ modified=@('index.html'); newLocal=@('new.css'); remoteHashes=@{'index.html'=$script:testOldHash} }
}
function Get-RemoteSha256($Session, [string]$RemoteFile) {
    $local = Join-Path $script:testRemoteRoot ($RemoteFile.Substring('/test-root/'.Length) -replace '/', '\')
    (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Open-SiteSession([string]$Domain, $Config) {
    $mock = [pscustomobject]@{ RemoteRoot=$script:testRemoteRoot }
    $mock | Add-Member ScriptMethod FileExists { param($remotePath)
        $relative = $remotePath.Substring('/test-root/'.Length) -replace '/', '\'
        Test-Path -LiteralPath (Join-Path $this.RemoteRoot $relative)
    }
    $mock | Add-Member ScriptMethod CreateDirectory { param($remotePath)
        $relative = $remotePath.Substring('/test-root/'.Length) -replace '/', '\'
        New-Item -ItemType Directory -Path (Join-Path $this.RemoteRoot $relative) -Force | Out-Null
    }
    $mock | Add-Member ScriptMethod PutFileToDirectory { param($localFile, $remoteDirectory, $remove, $options)
        $targetDirectory = $this.RemoteRoot
        if ($remoteDirectory -ne '/test-root') {
            $relative = $remoteDirectory.Substring('/test-root/'.Length) -replace '/', '\'
            $targetDirectory = Join-Path $this.RemoteRoot $relative
        }
        New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        Copy-Item -LiteralPath $localFile -Destination (Join-Path $targetDirectory (Split-Path $localFile -Leaf))
        return [pscustomobject]@{ Error=$null }
    }
    $mock | Add-Member ScriptMethod GetFiles { param($remoteFileMask, $localFile, $remove)
        $remoteFile = $remoteFileMask -replace '\\([*?\[\]])', '$1'
        $relative = $remoteFile.Substring('/test-root/'.Length) -replace '/', '\'
        Copy-Item -LiteralPath (Join-Path $this.RemoteRoot $relative) -Destination $localFile
        $result = [pscustomobject]@{}
        $result | Add-Member ScriptMethod Check {}
        return $result
    }
    $mock | Add-Member ScriptMethod Dispose {}
    return $mock
}

try {
    $root = $testRoot
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $settingsPath = Join-Path $testRoot 'settings.json'
    '{"publishEnabled":true}' | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    $local = Join-Path $testRoot 'local'
    $script:testRemoteRoot = Join-Path $testRoot 'remote'
    $script:testSnapshot = Join-Path $testRoot 'snapshot'
    foreach ($dir in @($local, $script:testRemoteRoot, $script:testSnapshot)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    'versao-antiga' | Set-Content -LiteralPath (Join-Path $script:testRemoteRoot 'index.html') -Encoding UTF8
    Copy-Item -LiteralPath (Join-Path $script:testRemoteRoot 'index.html') -Destination (Join-Path $script:testSnapshot 'index.html')
    'versao-nova' | Set-Content -LiteralPath (Join-Path $local 'index.html') -Encoding UTF8
    'novo-css' | Set-Content -LiteralPath (Join-Path $local 'new.css') -Encoding UTF8
    $script:testOldHash = (Get-FileHash -LiteralPath (Join-Path $script:testRemoteRoot 'index.html') -Algorithm SHA256).Hash.ToLowerInvariant()
    $config = [pscustomobject]@{ localRoot=$local; remoteRoot='/test-root'; host='127.0.0.1'; protocol='sftp' }
    $result = Publish-ChangedFiles 'example.com.br' $config $local
    if ($result.status -ne 'ok' -or $result.uploaded.Count -ne 2 -or $result.deleted.Count -ne 0) { throw 'Resultado do envio incorreto.' }
    $savedOld = Join-Path $result.backup 'index.html'
    if ((Get-FileHash -LiteralPath $savedOld -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:testOldHash) { throw 'Backup anterior divergente.' }
    foreach ($name in @('index.html','new.css')) {
        $localHash = (Get-FileHash -LiteralPath (Join-Path $local $name) -Algorithm SHA256).Hash
        $remoteHash = (Get-FileHash -LiteralPath (Join-Path $script:testRemoteRoot $name) -Algorithm SHA256).Hash
        if ($localHash -ne $remoteHash) { throw "Arquivo de teste divergente: $name" }
    }
    $before = (Get-FileHash -LiteralPath (Join-Path $script:testRemoteRoot 'index.html') -Algorithm SHA256).Hash
    $stopped = $false
    try { $null = Publish-ChangedFiles 'example.com.br' $config $local }
    catch { $stopped = $_.Exception.Message -like '*mudou apos a comparacao*' }
    if (-not $stopped) { throw 'Mudanca remota concorrente nao bloqueou o envio.' }
    if ((Get-FileHash -LiteralPath (Join-Path $script:testRemoteRoot 'index.html') -Algorithm SHA256).Hash -ne $before) { throw 'Arquivo remoto mudou durante bloqueio.' }
    if (Test-PublishablePath '.env') { throw 'Arquivo sensivel nao foi bloqueado.' }
    'OK: backup anterior, dois arquivos enviados, hashes iguais, nenhuma exclusao.'
}
finally {
    if (Test-Path -LiteralPath $resolvedTest) { Remove-Item -LiteralPath $resolvedTest -Recurse -Force }
}
