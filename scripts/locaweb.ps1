param(
    [Parameter(Position=0)][ValidateSet('help','list','info','register','credential','scan-key','set-key','test','files','download','backup','diff','dry-run','deploy')][string]$Command = 'help',
    [Parameter(Position=1)][string]$Site,
    [string]$Path = '',
    [string]$Protocol,
    [string]$HostName,
    [int]$Port,
    [string]$UserName,
    [string]$RemoteRoot,
    [string]$LocalRoot,
    [string]$SourcePath,
    [string]$SshHostKeyFingerprint,
    [switch]$AllowPlainFtp
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sitesPath = Join-Path $root 'config\sites.json'
$settingsPath = Join-Path $root 'config\settings.json'
$credentialDirectory = Join-Path $env:LOCALAPPDATA 'Codex-Locaweb-SFTP\credentials'
$logPath = Join-Path $root 'logs\operacoes.jsonl'

function Write-Result([System.Collections.IDictionary]$Value) {
    $Value | ConvertTo-Json -Depth 10 -Compress
}

function Write-Operation([string]$Action, [string]$Domain, [string]$Status, [string]$Detail) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
    $entry = [ordered]@{ at = (Get-Date).ToString('o'); site = $Domain; action = $Action; status = $Status; detail = $Detail }
    $entry | ConvertTo-Json -Compress | Add-Content -LiteralPath $logPath -Encoding UTF8
}

function Assert-Domain([string]$Domain) {
    if ($Domain -notmatch '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$') {
        throw 'Dominio invalido. Use somente o nome DNS, sem caminho ou URL.'
    }
}

function Get-Sites {
    Get-Content -LiteralPath $sitesPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-Site([string]$Domain) {
    Assert-Domain $Domain
    $all = Get-Sites
    $property = $all.PSObject.Properties[$Domain]
    if (-not $property) { throw "Dominio nao cadastrado: $Domain" }
    $property.Value
}

function Get-CredentialPath([string]$Domain) {
    Assert-Domain $Domain
    Join-Path $credentialDirectory ($Domain + '.dpapi')
}

function Assert-Config([string]$Domain, $Config) {
    Assert-Domain $Domain
    if ($Config.protocol -notin @('sftp','ftps','ftp')) { throw 'Protocolo deve ser sftp, ftps ou ftp.' }
    if ([string]::IsNullOrWhiteSpace($Config.host) -or [string]::IsNullOrWhiteSpace($Config.username)) { throw 'Host e usuario sao obrigatorios.' }
    if ($Config.port -lt 1 -or $Config.port -gt 65535) { throw 'Porta invalida.' }
    if (-not $Config.remoteRoot.StartsWith('/') -or $Config.remoteRoot -match '(^|/)\.\.?(/|$)|\\') { throw 'remoteRoot deve ser um caminho absoluto Unix sem segmentos . ou ...' }
    if ([string]::IsNullOrWhiteSpace($Config.localRoot) -or -not [IO.Path]::IsPathRooted($Config.localRoot)) { throw 'localRoot deve ser absoluto.' }
    if ($Config.protocol -eq 'sftp' -and ([string]::IsNullOrWhiteSpace($Config.sshHostKeyFingerprint) -or $Config.sshHostKeyFingerprint -eq '*')) { throw 'Falta a impressao digital SSH confirmada. Nao sera aceita qualquer chave.' }
    if ($Config.protocol -eq 'ftp' -and -not $Config.allowPlainFtp) { throw 'FTP sem criptografia exige allowPlainFtp=true explicito.' }
}

function Open-SiteSession([string]$Domain, $Config) {
    Assert-Config $Domain $Config
    $secretPath = Get-CredentialPath $Domain
    if (-not (Test-Path -LiteralPath $secretPath)) { throw "Credencial ausente. Execute: .\scripts\locaweb.ps1 credential $Domain" }
    $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $assembly = Join-Path $settings.winscpDirectory 'WinSCPnet.dll'
    $executable = Join-Path $settings.winscpDirectory 'WinSCP.exe'
    if (-not (Test-Path -LiteralPath $assembly) -or -not (Test-Path -LiteralPath $executable)) { throw 'WinSCP nao encontrado no diretorio configurado.' }
    Add-Type -Path $assembly
    $secure = (Get-Content -LiteralPath $secretPath -Raw -Encoding UTF8).Trim() | ConvertTo-SecureString
    try {
        $options = New-Object WinSCP.SessionOptions
        $options.HostName = $Config.host
        $options.PortNumber = [int]$Config.port
        $options.UserName = $Config.username
        $options.SecurePassword = $secure
        switch ($Config.protocol) {
            'sftp' { $options.Protocol = [WinSCP.Protocol]::Sftp; $options.SshHostKeyFingerprint = $Config.sshHostKeyFingerprint }
            'ftps' { $options.Protocol = [WinSCP.Protocol]::Ftp; $options.FtpSecure = [WinSCP.FtpSecure]::Explicit }
            'ftp' { $options.Protocol = [WinSCP.Protocol]::Ftp; $options.FtpSecure = [WinSCP.FtpSecure]::None }
        }
        $session = New-Object WinSCP.Session
        $session.ExecutablePath = $executable
        try { $session.Open($options); return $session }
        catch { $session.Dispose(); throw }
    }
    finally { $secure.Dispose() }
}

function Resolve-RemotePath($Config, [string]$Relative) {
    $base = $Config.remoteRoot.TrimEnd('/')
    if ([string]::IsNullOrWhiteSpace($Relative)) { return $base }
    if ($Relative.StartsWith('/') -or $Relative -match '(^|/)\.\.?(/|$)|\\|[\*\?]') { throw 'Caminho remoto deve ser relativo ao remoteRoot, sem .. ou curingas.' }
    $base + '/' + $Relative.TrimEnd('/')
}

function Save-RemoteSnapshot([string]$Domain, $Config, [string]$Relative, [string]$Kind) {
    $remote = Resolve-RemotePath $Config $Relative
    $session = Open-SiteSession $Domain $Config
    try {
        $info = $session.GetFileInfo($remote)
        $stamp = (Get-Date).ToString('yyyy-MM-dd_HH-mm-ss_fff') + '_' + [guid]::NewGuid().ToString('N').Substring(0,8)
        $base = Join-Path (Join-Path $root $Kind) $Domain
        $destination = Join-Path $base $stamp
        if (Test-Path -LiteralPath $destination) { throw 'Destino de snapshot ja existe; nada foi sobrescrito.' }
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        try {
            if ($info.IsDirectory) {
                $localTarget = $destination
                if ($Relative) {
                    $localTarget = Join-Path $destination ($Relative -replace '/', '\')
                    New-Item -ItemType Directory -Path $localTarget -Force | Out-Null
                }
                $result = $session.GetFiles(($remote.TrimEnd('/') + '/*'), ($localTarget.TrimEnd('\') + '\'), $false)
            }
            else {
                $localTarget = Join-Path $destination ($Relative -replace '/', '\')
                New-Item -ItemType Directory -Path (Split-Path $localTarget) -Force | Out-Null
                $result = $session.GetFiles([WinSCP.RemotePath]::EscapeFileMask($remote), $localTarget, $false)
            }
            $result.Check()
            $files = @(Get-ChildItem -LiteralPath $destination -File -Recurse -Force | Sort-Object FullName | ForEach-Object {
                [ordered]@{ path=$_.FullName.Substring($destination.Length + 1).Replace('\','/'); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
            })
            $total = [long]0
            foreach ($file in $files) { $total += $file.bytes }
            $manifest = [ordered]@{ status='complete'; site=$Domain; protocol=$Config.protocol; host=$Config.host; remoteRoot=$Config.remoteRoot; selectedPath=$Relative; createdAt=(Get-Date).ToString('o'); fileCount=$files.Count; bytes=$total; files=$files }
            $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $destination 'manifest.json') -Encoding UTF8
            $operation = if ($Kind -eq 'backups') { 'backup' } else { 'download' }
            Write-Operation $operation $Domain 'success' ("$($files.Count) arquivos; $total bytes; $destination")
            return @{ status='ok'; operation=$operation; site=$Domain; remotePath=$remote; destination=$destination; files=$files.Count; bytes=$total }
        }
        catch {
            'Download incompleto. Nao usar como backup.' | Set-Content -LiteralPath (Join-Path $destination 'INCOMPLETO.txt') -Encoding UTF8
            throw
        }
    }
    finally { $session.Dispose() }
}

function Compare-SiteFiles([string]$Domain, $Config) {
    if (-not (Test-Path -LiteralPath $Config.localRoot -PathType Container)) { throw 'Diretorio local do site nao existe.' }
    $localBase = [IO.Path]::GetFullPath($Config.localRoot).TrimEnd('\')
    $remoteBase = $Config.remoteRoot.TrimEnd('/')
    $changed = New-Object 'System.Collections.Generic.List[string]'
    $localOnly = New-Object 'System.Collections.Generic.List[string]'
    $remoteOnly = New-Object 'System.Collections.Generic.List[string]'
    $conflicts = New-Object 'System.Collections.Generic.List[string]'
    $remoteHashes = @{}
    $session = Open-SiteSession $Domain $Config
    try {
        $options = New-Object WinSCP.TransferOptions
        $differences = $session.CompareDirectories([WinSCP.SynchronizationMode]::Remote, $localBase, $remoteBase, $true, $true, [WinSCP.SynchronizationCriteria]::Checksum, $options)
        foreach ($difference in $differences) {
            $action = $difference.Action.ToString()
            if ($action -eq 'UploadNew') {
                if ($difference.IsDirectory) {
                    foreach ($file in @(Get-ChildItem -LiteralPath $difference.Local.FileName -File -Recurse -Force)) {
                        $localOnly.Add($file.FullName.Substring($localBase.Length + 1).Replace('\','/'))
                    }
                }
                else { $localOnly.Add($difference.Local.FileName.Substring($localBase.Length + 1).Replace('\','/')) }
            }
            elseif ($action -eq 'UploadUpdate') {
                if ($difference.IsDirectory) { throw 'Comparacao encontrou atualizacao de pasta inesperada.' }
                $relative = $difference.Local.FileName.Substring($localBase.Length + 1).Replace('\','/')
                if ($difference.Local.LastWriteTime.ToUniversalTime() -le $difference.Remote.LastWriteTime.ToUniversalTime().AddSeconds(2)) {
                    $conflicts.Add($relative)
                }
                else {
                    $changed.Add($relative)
                    $remoteHashes[$relative] = Get-RemoteSha256 $session $difference.Remote.FileName
                }
            }
            elseif ($action -eq 'DeleteRemote') {
                $remoteOnly.Add($difference.Remote.FileName.Substring($remoteBase.Length + 1) + $(if ($difference.IsDirectory) { '/' } else { '' }))
            }
            else { throw "Acao de comparacao inesperada: $action" }
        }
    }
    finally { $session.Dispose() }
    $localCount = @(Get-ChildItem -LiteralPath $localBase -File -Recurse -Force).Count
    $equal = $localCount - $changed.Count - $localOnly.Count - $conflicts.Count
    $comparison = [ordered]@{ status='ok'; operation='diff'; site=$Domain; host=$Config.host; remoteRoot=$Config.remoteRoot; localRoot=$Config.localRoot; method='server-checksum'; equal=$equal; modified=@($changed.ToArray() | Sort-Object); newLocal=@($localOnly.ToArray() | Sort-Object); newRemote=@($remoteOnly.ToArray() | Sort-Object); conflicts=@($conflicts.ToArray() | Sort-Object); remoteHashes=$remoteHashes }
    Write-Operation 'diff' $Domain 'success' ("iguais=$equal; alterados=$($changed.Count); so-local=$($localOnly.Count); so-remoto=$($remoteOnly.Count); conflitos=$($conflicts.Count)")
    return $comparison
}

function Test-PublishablePath([string]$Relative) {
    $segments = $Relative.ToLowerInvariant().Split('/')
    foreach ($segment in $segments) {
        if ($segment -in @('.git','.vscode','.idea','node_modules','backups','logs','.openai','__pycache__')) { return $false }
    }
    $leaf = $segments[-1]
    if ($leaf -in @('.gitignore','.env','wp-config.php','configuration.php','config.php','.htaccess','web.config','agents.md')) { return $false }
    if ($leaf -match '(?i)\.(bak|backup|tmp|temp|log|pem|key|pfx|p12|ps1)$') { return $false }
    return $true
}

function Assert-ExactLocalRoot($Config, [string]$RequestedPath) {
    if ([string]::IsNullOrWhiteSpace($RequestedPath)) { $RequestedPath = $Config.localRoot }
    $expected = [IO.Path]::GetFullPath($Config.localRoot).TrimEnd('\')
    $requested = [IO.Path]::GetFullPath($RequestedPath).TrimEnd('\')
    if (-not [string]::Equals($expected, $requested, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Pasta local diferente da cadastrada para este dominio. Esperada: $expected"
    }
    if (-not (Test-Path -LiteralPath $expected -PathType Container)) { throw 'Pasta local cadastrada nao existe.' }
    $rootItem = Get-Item -LiteralPath $expected -Force
    if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Pasta local e uma juncao ou link; envio bloqueado.' }
    $linked = @(Get-ChildItem -LiteralPath $expected -Recurse -Force -ErrorAction Stop | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1)
    if ($linked.Count) { throw 'A pasta local contem juncao ou link; envio bloqueado.' }
    return $expected
}

function Get-RemoteSha256($Session, [string]$RemoteFile) {
    $bytes = $Session.CalculateFileChecksum('sha-256', $RemoteFile)
    [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

function Publish-ChangedFiles([string]$Domain, $Config, [string]$RequestedPath) {
    $source = Assert-ExactLocalRoot $Config $RequestedPath
    $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $settings.publishEnabled) { throw 'Publicacao esta desativada na configuracao.' }
    $comparison = Compare-SiteFiles $Domain $Config
    if ($comparison.conflicts.Count) { throw ('Arquivos locais diferentes mas nao mais recentes que o servidor: ' + ($comparison.conflicts -join ', ')) }
    $candidates = @($comparison.modified) + @($comparison.newLocal)
    $blocked = @($candidates | Where-Object { -not (Test-PublishablePath $_) } | Sort-Object)
    if ($blocked.Count) { throw ('Arquivos alterados bloqueados para publicacao: ' + ($blocked -join ', ')) }
    if (-not $candidates.Count) {
        Write-Operation 'deploy' $Domain 'success' 'Nenhuma diferenca local; nenhum envio.'
        return @{ status='ok'; operation='deploy'; site=$Domain; uploaded=@(); deleted=@(); message='Nenhuma diferenca local.' }
    }
    $remoteSnapshot = $comparison.remoteHashes
    $stamp = (Get-Date).ToString('yyyy-MM-dd_HH-mm-ss_fff') + '_' + [guid]::NewGuid().ToString('N').Substring(0,8)
    $backupRoot = Join-Path (Join-Path (Join-Path $root 'backups') $Domain) ('antes-envio_' + $stamp)
    New-Item -ItemType Directory -Path $backupRoot -ErrorAction Stop | Out-Null
    $uploaded = New-Object 'System.Collections.Generic.List[string]'
    $session = Open-SiteSession $Domain $Config
    try {
        # Todos os arquivos que seriam substituidos sao copiados antes do primeiro envio.
        foreach ($relative in @($comparison.modified | Sort-Object)) {
            $remoteFile = Resolve-RemotePath $Config $relative
            if ((Get-RemoteSha256 $session $remoteFile) -ne $remoteSnapshot[$relative]) { throw "Arquivo remoto mudou apos a comparacao: $relative" }
            $backupFile = Join-Path $backupRoot ($relative -replace '/', '\')
            New-Item -ItemType Directory -Path (Split-Path $backupFile) -Force | Out-Null
            $backupTransfer = $session.GetFiles([WinSCP.RemotePath]::EscapeFileMask($remoteFile), $backupFile, $false)
            $backupTransfer.Check()
            $copiedHash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($copiedHash -ne $remoteSnapshot[$relative]) { throw "Copia de seguranca falhou: $relative" }
        }
        foreach ($relative in @($comparison.newLocal | Sort-Object)) {
            $remoteFile = Resolve-RemotePath $Config $relative
            if ($session.FileExists($remoteFile)) { throw "Arquivo remoto surgiu apos a comparacao: $relative" }
        }
        $backupManifest = [ordered]@{ status='complete'; operation='before-deploy'; site=$Domain; createdAt=(Get-Date).ToString('o'); remoteRoot=$Config.remoteRoot; source=$source; overwrite=@($comparison.modified | Sort-Object); create=@($comparison.newLocal | Sort-Object); files=@($comparison.modified | Sort-Object | ForEach-Object { [ordered]@{ path=$_; sha256=$remoteSnapshot[$_] } }) }
        $backupManifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $backupRoot 'manifest.json') -Encoding UTF8
        $transfer = New-Object WinSCP.TransferOptions
        $transfer.TransferMode = [WinSCP.TransferMode]::Binary
        $transfer.ResumeSupport.State = [WinSCP.TransferResumeSupportState]::On
        foreach ($relative in @($candidates | Sort-Object)) {
            $localFile = Join-Path $source ($relative -replace '/', '\')
            if (-not (Test-Path -LiteralPath $localFile -PathType Leaf)) { throw "Arquivo local desapareceu: $relative" }
            $localHash = (Get-FileHash -LiteralPath $localFile -Algorithm SHA256).Hash.ToLowerInvariant()
            $remoteFile = Resolve-RemotePath $Config $relative
            if ($remoteSnapshot.ContainsKey($relative)) {
                if ((Get-RemoteSha256 $session $remoteFile) -ne $remoteSnapshot[$relative]) { throw "Arquivo remoto mudou antes do envio: $relative" }
            }
            elseif ($session.FileExists($remoteFile)) { throw "Arquivo remoto surgiu antes do envio: $relative" }
            $remoteDirectory = [WinSCP.RemotePath]::GetDirectoryName($remoteFile)
            $segments = $relative.Split('/')
            $current = $Config.remoteRoot.TrimEnd('/')
            for ($i=0; $i -lt ($segments.Length - 1); $i++) {
                $current += '/' + $segments[$i]
                if (-not $session.FileExists($current)) { $session.CreateDirectory($current) }
            }
            $result = $session.PutFileToDirectory($localFile, $remoteDirectory, $false, $transfer)
            if ($result.Error) { throw $result.Error }
            if ((Get-RemoteSha256 $session $remoteFile) -ne $localHash) { throw "Hash remoto divergente apos envio: $relative" }
            $uploaded.Add($relative)
            Write-Operation 'deploy-file' $Domain 'success' $relative
        }
        $done = [ordered]@{ status='complete'; operation='deploy'; site=$Domain; createdAt=(Get-Date).ToString('o'); backup=$backupRoot; uploaded=@($uploaded.ToArray()); deleted=@() }
        $done | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupRoot 'deploy-result.json') -Encoding UTF8
        Write-Operation 'deploy' $Domain 'success' ("enviados=$($uploaded.Count); backup=$backupRoot; exclusoes=0")
        return @{ status='ok'; operation='deploy'; site=$Domain; host=$Config.host; remoteRoot=$Config.remoteRoot; backup=$backupRoot; uploaded=@($uploaded.ToArray()); deleted=@() }
    }
    catch {
        $partial = [ordered]@{ status='incomplete'; operation='deploy'; site=$Domain; at=(Get-Date).ToString('o'); uploaded=@($uploaded.ToArray()); backup=$backupRoot; message=$_.Exception.Message }
        $partial | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupRoot 'deploy-result.json') -Encoding UTF8
        Write-Operation 'deploy' $Domain 'error' ("enviados=$($uploaded.Count); backup=$backupRoot; erro=$($_.Exception.Message)")
        throw
    }
    finally { $session.Dispose() }
}

try {
    if ($Site) { $Site = $Site.Trim().ToLowerInvariant() }
    switch ($Command) {
        'help' {
            Write-Result @{ status='ok'; commands=@('list','info <dominio>','register <dominio> -Protocol sftp -HostName ... -Port 22 -UserName ... -RemoteRoot /... -LocalRoot C:\...','credential <dominio>','scan-key <dominio>','set-key <dominio> -SshHostKeyFingerprint <chave-confirmada>','test <dominio>','files <dominio> [-Path pasta/relativa]','download <dominio> [-Path pasta/relativa]','backup <dominio> [-Path pasta/relativa]','diff <dominio>','dry-run <dominio>','deploy <dominio>'); stage='Comparacao exata por SHA-256; envio sem exclusoes e com backup previo' }
        }
        'list' {
            $all = Get-Sites
            $items = @($all.PSObject.Properties | Sort-Object Name | ForEach-Object {
                $c = $_.Value
                [ordered]@{ site=$_.Name; protocol=$c.protocol; host=$c.host; port=$c.port; remoteRoot=$c.remoteRoot; credentialRegistered=(Test-Path -LiteralPath (Get-CredentialPath $_.Name)); hostKeyRegistered=([bool]$c.sshHostKeyFingerprint) }
            })
            Write-Result @{ status='ok'; operation='list'; sites=$items }
        }
        'info' {
            $config = Get-Site $Site
            Write-Result @{ status='ok'; operation='info'; site=$Site; protocol=$config.protocol; host=$config.host; username=$config.username; remoteRoot=$config.remoteRoot; localRoot=$config.localRoot; credentialRegistered=(Test-Path -LiteralPath (Get-CredentialPath $Site)); hostKeyRegistered=([bool]$config.sshHostKeyFingerprint) }
        }
        'register' {
            Assert-Domain $Site
            if (-not $Protocol -or -not $HostName -or -not $Port -or -not $UserName -or -not $RemoteRoot -or -not $LocalRoot) { throw 'Informe protocolo, host, porta, usuario, remoteRoot e localRoot.' }
            $all = Get-Sites
            if ($all.PSObject.Properties[$Site]) { throw 'Dominio ja cadastrado; cadastro existente preservado.' }
            $config = [pscustomobject]@{ protocol=$Protocol.ToLowerInvariant(); host=$HostName; port=$Port; username=$UserName; remoteRoot=$RemoteRoot; localRoot=$LocalRoot; sshHostKeyFingerprint=$SshHostKeyFingerprint; allowPlainFtp=[bool]$AllowPlainFtp }
            # Cadastro pode ficar pendente da chave SSH; as conexoes continuam bloqueadas ate la.
            if ($config.protocol -notin @('sftp','ftps','ftp')) { throw 'Protocolo invalido.' }
            if ($config.remoteRoot -notmatch '^/' -or $config.remoteRoot -match '(^|/)\.\.?(/|$)|\\') { throw 'remoteRoot invalido.' }
            $all | Add-Member -MemberType NoteProperty -Name $Site -Value $config
            $all | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $sitesPath -Encoding UTF8
            Write-Operation 'register' $Site 'success' 'Configuracao sem senha cadastrada.'
            Write-Result @{ status='ok'; operation='register'; site=$Site }
        }
        'credential' {
            $null = Get-Site $Site
            $secret = Read-Host "Senha do site $Site" -AsSecureString
            if (-not $secret -or $secret.Length -eq 0) { throw 'Senha vazia; nada foi salvo.' }
            New-Item -ItemType Directory -Path $credentialDirectory -Force | Out-Null
            $encrypted = ConvertFrom-SecureString -SecureString $secret
            Set-Content -LiteralPath (Get-CredentialPath $Site) -Value $encrypted -Encoding UTF8
            $secret.Dispose()
            Write-Operation 'credential' $Site 'success' 'Credencial DPAPI cadastrada; senha omitida.'
            Write-Result @{ status='ok'; operation='credential'; site=$Site }
        }
        'scan-key' {
            $config = Get-Site $Site
            if ($config.protocol -ne 'sftp') { throw 'scan-key exige SFTP.' }
            $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            Add-Type -Path (Join-Path $settings.winscpDirectory 'WinSCPnet.dll')
            $options = New-Object WinSCP.SessionOptions
            $options.Protocol = [WinSCP.Protocol]::Sftp
            $options.HostName = $config.host
            $options.PortNumber = [int]$config.port
            $session = New-Object WinSCP.Session
            $session.ExecutablePath = Join-Path $settings.winscpDirectory 'WinSCP.exe'
            try { $fingerprint = $session.ScanFingerprint($options, 'SHA-256') }
            finally { $session.Dispose() }
            Write-Operation 'scan-key' $Site 'success' 'Chave observada; nao foi aceita nem gravada.'
            Write-Result @{ status='ok'; operation='scan-key'; site=$Site; fingerprint=$fingerprint; verified=$false }
        }
        'set-key' {
            Assert-Domain $Site
            if ([string]::IsNullOrWhiteSpace($SshHostKeyFingerprint) -or $SshHostKeyFingerprint -eq '*' -or $SshHostKeyFingerprint -notmatch '^ssh-') { throw 'Informe uma chave SSH especifica, confirmada por canal confiavel.' }
            $all = Get-Sites
            $property = $all.PSObject.Properties[$Site]
            if (-not $property) { throw "Dominio nao cadastrado: $Site" }
            $property.Value.sshHostKeyFingerprint = $SshHostKeyFingerprint
            $all | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $sitesPath -Encoding UTF8
            Write-Operation 'set-key' $Site 'success' 'Impressao digital SSH cadastrada.'
            Write-Result @{ status='ok'; operation='set-key'; site=$Site }
        }
        'test' {
            $config = Get-Site $Site
            $session = Open-SiteSession $Site $config
            try { $listing = $session.ListDirectory((Resolve-RemotePath $config '')); $count = @($listing.Files | Where-Object { $_.Name -notin @('.','..') }).Count }
            finally { $session.Dispose() }
            Write-Operation 'test' $Site 'success' 'Conexao e diretorio remoto verificados.'
            Write-Result @{ status='ok'; operation='test'; site=$Site; host=$config.host; remoteRoot=$config.remoteRoot; items=$count }
        }
        'files' {
            $config = Get-Site $Site
            $target = Resolve-RemotePath $config $Path
            $session = Open-SiteSession $Site $config
            try { $listing = $session.ListDirectory($target); $items = @($listing.Files | Where-Object { $_.Name -notin @('.','..') } | ForEach-Object { [ordered]@{ name=$_.Name; isDirectory=$_.IsDirectory; length=$_.Length; modified=$_.LastWriteTime.ToString('o') } }) }
            finally { $session.Dispose() }
            Write-Operation 'files' $Site 'success' $target
            Write-Result @{ status='ok'; operation='files'; site=$Site; path=$target; files=$items }
        }
        'download' {
            $config = Get-Site $Site
            Write-Result (Save-RemoteSnapshot $Site $config $Path 'sites')
        }
        'backup' {
            $config = Get-Site $Site
            Write-Result (Save-RemoteSnapshot $Site $config $Path 'backups')
        }
        'diff' {
            $config = Get-Site $Site
            Write-Result (Compare-SiteFiles $Site $config)
        }
        'dry-run' {
            $config = Get-Site $Site
            $comparison = Compare-SiteFiles $Site $config
            $toUpload = @($comparison.newLocal) + @($comparison.modified)
            $blocked = @($toUpload | Where-Object { -not (Test-PublishablePath $_) } | Sort-Object)
            $eligible = @($toUpload | Where-Object { Test-PublishablePath $_ } | Sort-Object)
            Write-Operation 'dry-run' $Site 'success' ("candidatos=$($eligible.Count); bloqueados=$($blocked.Count); exclusoes=0")
            $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Result @{ status='ok'; operation='dry-run'; site=$Site; host=$config.host; remoteRoot=$config.remoteRoot; localRoot=$config.localRoot; method=$comparison.method; newLocal=$comparison.newLocal; modified=$comparison.modified; remoteOnly=$comparison.newRemote; conflicts=$comparison.conflicts; wouldUpload=$eligible; blocked=$blocked; wouldDelete=@(); publishEnabled=[bool]$settings.publishEnabled }
        }
        'deploy' {
            $config = Get-Site $Site
            Write-Result (Publish-ChangedFiles $Site $config $SourcePath)
        }
    }
}
catch {
    $message = $_.Exception.Message -replace '(?i)(password|passphrase|token|senha)\s*[=:]\s*\S+', '$1=[OCULTO]'
    Write-Operation $Command $Site 'error' $message
    Write-Result @{ status='error'; operation=$Command; site=$Site; message=$message }
    exit 1
}
