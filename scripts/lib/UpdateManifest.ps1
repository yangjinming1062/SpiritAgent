# scripts/lib/UpdateManifest.ps1 —— 自更新签名、manifest 与 update zip 助手；build.py 在 Windows 收尾阶段调用 Build-UpdateZip。

# Git for Windows 的 openssl 不在 PATH，故额外查找常见安装位置。
function Resolve-OpenSsl {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        Join-Path $env:ProgramFiles 'Git\mingw64\bin\openssl.exe'
        Join-Path ${env:ProgramFiles(x86)} 'Git\mingw64\bin\openssl.exe'
        Join-Path $env:LOCALAPPDATA 'Programs\Git\mingw64\bin\openssl.exe'
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }

    throw "openssl not found. Install Git for Windows (includes openssl) or add openssl to PATH."
}

# 签名私钥放仓库外；环境变量传 PEM 路径，缺省读 ~/.spiritagent/update.key（配置见 scripts/release-keys/README.md）。
function Resolve-UpdateSigningKey {
    [CmdletBinding()]
    param()

    if ($env:SPIRITAGENT_UPDATE_SIGNING_KEY) {
        $explicit = $env:SPIRITAGENT_UPDATE_SIGNING_KEY
        if (-not (Test-Path -LiteralPath $explicit)) {
            throw "SPIRITAGENT_UPDATE_SIGNING_KEY=$explicit does not exist."
        }

        return (Resolve-Path -LiteralPath $explicit).Path
    }

    $default = Join-Path $HOME '.spiritagent\update.key'
    if (Test-Path -LiteralPath $default) {
        return (Resolve-Path -LiteralPath $default).Path
    }

    throw "Release signing key not found. Set SPIRITAGENT_UPDATE_SIGNING_KEY to the PEM key path, " +
          "or place the key at $default."
}

# 就地签署更新清单：对 "<path>|<sha512>" 签名并回写 sha512/files/signature；缺 path、缺文件或无 openssl 时抛错。
function Sign-Manifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$KeyPath,
        # 调用方已算好 SHA-512/大小时传入，避免对大 wheel 重复哈希。
        [string]$Sha512 = '',
        [long]$Size = -1
    )

    if (-not (Test-Path $ManifestPath)) {
        Write-Warning "Manifest $ManifestPath does not exist; skipping"
        return
    }

    $manifestDir = Split-Path -Parent $ManifestPath
    $raw = Get-Content $ManifestPath -Raw

    # 正则提取 path，JSON 与 YAML 共用，避免引入 YAML 解析器。
    $pathMatch = [regex]::Match($raw, '(?m)^\s*"?\s*path\s*"?\s*[:=]\s*"?\s*(.+?)\s*"?\s*,?\s*$')
    if (-not $pathMatch.Success) {
        Write-Warning "Manifest $ManifestPath has no 'path' field; skipping"
        return
    }
    $pathValue = $pathMatch.Groups[1].Value.Trim('"', "'", ',')

    $binaryPath = Join-Path $manifestDir $pathValue
    if (-not (Test-Path $binaryPath)) {
        throw "Manifest $ManifestPath references missing file: $binaryPath"
    }

    if ($Sha512 -and $Size -ge 0) {
        $sha512 = $Sha512.ToUpper()
        $size = $Size
    } else {
        $sha512 = (Get-FileHash $binaryPath -Algorithm SHA512).Hash.ToUpper()
        $size = (Get-Item $binaryPath).Length
    }

    # 临时文件落地签名：PowerShell `&` 把二进制 stdout 当数组收，无法直接成字节流。
    $openssl = Resolve-OpenSsl
    $payload = "$pathValue|$sha512"
    $tmpPayload = Join-Path ([IO.Path]::GetTempPath()) "spiritagent-sign-payload-$PID.tmp"
    $tmpSig = Join-Path ([IO.Path]::GetTempPath()) "spiritagent-sign-sig-$PID.tmp"
    try {
        [IO.File]::WriteAllBytes($tmpPayload, [Text.Encoding]::UTF8.GetBytes($payload))
        & $openssl dgst -sha512 -sign $KeyPath -out $tmpSig $tmpPayload 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "openssl sign failed for $ManifestPath (exit $LASTEXITCODE)"
        }
        $signatureBytes = [IO.File]::ReadAllBytes($tmpSig)
    } finally {
        Remove-Item $tmpPayload, $tmpSig -ErrorAction SilentlyContinue
    }
    $signatureB64 = [Convert]::ToBase64String($signatureBytes)

    # 正则就地改字段，兼容 YAML 与 JSON。
    $updated = $raw

    # sha512:  sha512: <value>  or  "sha512": "<value>"
    $updated = $updated -replace '(?m)(\s*"?\s*sha512\s*"?\s*[:=]\s*"?\s*)[^\r\n"]*', "`${1}$sha512"

    # files 数组整段替换为单元素数组。
    $filesBlockYaml = "  - url: $pathValue`n    sha512: $sha512`n    size: $size"
    $filesBlockJson = "`"files`": [{`"url`": `"$pathValue`", `"sha512`": `"$sha512`", `"size`": $size}]"
    if ($updated -match '(?m)^\s*"?\s*files\s*"?\s*:\s*\[') {
        $updated = $updated -replace '(?m)(\s*"?\s*files\s*"?\s*:\s*)\[.*?\]', "`${1}$filesBlockJson"
    } else {
        $updated = $updated -replace '(?s)(files:\s*\n)(\s+-[\s\S]*?)(?=\n\S|\n\n|\z)', "`${1}$filesBlockYaml"
    }

    if ($updated -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') {
        $updated = $updated -replace '(?m)(\s*"?\s*signature\s*"?\s*[:=]\s*"?\s*)[^\r\n"]*', "`${1}$signatureB64"
    } else {
        $updated = $updated.TrimEnd() + "`nsignature: $signatureB64`n"
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ManifestPath, $updated, $utf8NoBom)
}

# 为 runner wheel + server.py 构造并签出 latest-runner.yml（path 相对 manifest 目录）。
function New-RunnerManifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$WheelPath,
        [Parameter(Mandatory)][string]$ServerPyPath,
        [Parameter(Mandatory)][string]$OutDir,
        [Parameter(Mandatory)][string]$KeyPath
    )

    if (-not (Test-Path $WheelPath)) {
        throw "Wheel not found: $WheelPath"
    }
    if (-not (Test-Path $ServerPyPath)) {
        throw "server.py not found: $ServerPyPath"
    }

    $wheelName = Split-Path -Leaf $WheelPath
    $serverPyName = Split-Path -Leaf $ServerPyPath

    # 清单中的 path 相对 manifest 所在目录；manifest 写在 staging 根，path 为 runner/<wheel>。
    $wheelRel = "runner/$wheelName"
    $serverPyRel = "runner/$serverPyName"

    $stagedWheel = Join-Path $OutDir $wheelRel
    $stagedServer = Join-Path $OutDir $serverPyRel
    New-Item -ItemType Directory -Path (Split-Path $stagedWheel -Parent) -Force | Out-Null
    if (-not (Test-Path $stagedWheel)) {
        Copy-Item $WheelPath $stagedWheel -Force
    }
    if (-not (Test-Path $stagedServer)) {
        Copy-Item $ServerPyPath $stagedServer -Force
    }

    $wheelSha = (Get-FileHash $stagedWheel -Algorithm SHA512).Hash.ToUpper()
    $wheelSize = (Get-Item $stagedWheel).Length
    $serverPySha256 = (Get-FileHash $stagedServer -Algorithm SHA256).Hash.ToLower()

    $manifest = [ordered]@{
        version      = $Version
        path         = $wheelRel
        sha512       = $wheelSha
        size         = $wheelSize
        signature    = ''
        files        = @(@{ url = $wheelRel; sha512 = $wheelSha; size = $wheelSize })
        runner       = [ordered]@{
            version          = $Version
            wheel_filename   = $wheelRel
            wheel_sha512     = $wheelSha
            wheel_size       = $wheelSize
            server_py_sha256 = $serverPySha256
        }
    }

    $manifestPath = Join-Path $OutDir 'latest-runner.yml'
    ($manifest | ConvertTo-Json -Depth 8) | Set-Content -Path $manifestPath -NoNewline

    # 复用已算好的哈希，避免 Sign-Manifest 对大 wheel 重哈希。
    Sign-Manifest -ManifestPath $manifestPath -KeyPath $KeyPath -Sha512 $wheelSha -Size $wheelSize

    return $manifestPath
}

# 构造自更新 zip：桌面产物 + runner wheel + server.py 一次覆盖两侧；技能随桌面包交付、由 Client 同步到 Home。
function Build-UpdateZip {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$DesktopReleaseDir,
        [Parameter(Mandatory)][string]$RunnerWheelPath,
        [Parameter(Mandatory)][string]$ServerPyPath,
        [Parameter(Mandatory)][string]$OutputDir
    )

    if (-not (Test-Path $DesktopReleaseDir)) {
        throw "Desktop release dir not found: $DesktopReleaseDir — run `pnpm dist` first."
    }
    if (-not (Test-Path $RunnerWheelPath)) {
        throw "Runner wheel not found: $RunnerWheelPath"
    }
    if (-not (Test-Path $ServerPyPath)) {
        throw "server.py not found: $ServerPyPath"
    }

    Write-Output "==> Building update zip for $Version"

    $stageDir = Join-Path ([IO.Path]::GetTempPath()) "spiritagent-update-stage-$Version-$PID"
    if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
    New-Item -ItemType Directory -Path $stageDir | Out-Null

    try {
        # 仅拷贝当前版本产物与 manifest，剔除 win-unpacked/ 与旧构建残留。
        $versionPrefix = "SpiritAgent-$Version"
        Get-ChildItem -Path $DesktopReleaseDir -File | Where-Object {
            $_.Name -like "$versionPrefix*" -or
            $_.Name -match '^latest.*\.yml$' -or
            $_.Name -eq 'app-update.yml'
        } | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination $stageDir -Force
        }

        $runnerStage = Join-Path $stageDir 'runner'
        New-Item -ItemType Directory -Path $runnerStage -Force | Out-Null
        Copy-Item -Force $RunnerWheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath))
        Copy-Item -Force $ServerPyPath (Join-Path $runnerStage 'server.py')

        $keyPath = Resolve-UpdateSigningKey

        # 兜底重签 electron-builder 跨主机构建可能缺失的 manifest 签名。
        foreach ($name in @('latest.yml', 'latest-mac.yml')) {
            $manifestPath = Join-Path $stageDir $name
            if (-not (Test-Path $manifestPath)) { continue }
            $raw = Get-Content -Raw $manifestPath
            if ($raw -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') { continue }
            Write-Output "  re-signing $name (signature was missing)"
            Sign-Manifest -ManifestPath $manifestPath -KeyPath $keyPath
        }

        # latest-runner.yml：桌面端重启前拉取并校验 wheel + server.py。
        New-RunnerManifest `
            -Version $Version `
            -WheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath)) `
            -ServerPyPath (Join-Path $runnerStage 'server.py') `
            -OutDir $stageDir `
            -KeyPath $keyPath | Out-Null

        # manifest.json 指定规范 Windows NSIS exe，便于后端无歧义定位。
        $desktopExe = Get-ChildItem $stageDir -Filter 'SpiritAgent-*-win-*.exe' -File | Select-Object -First 1
        $manifest = [ordered]@{
            version        = $Version
            desktop_path   = if ($desktopExe) { $desktopExe.Name } else { $null }
            runner_wheel   = "runner/$(Split-Path -Leaf $RunnerWheelPath)"
            server_py      = 'runner/server.py'
            manifests      = @('latest.yml', 'latest-mac.yml', 'latest-runner.yml')
        }
        ($manifest | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $stageDir 'manifest.json') -NoNewline

        $zipPath = Join-Path $OutputDir "SpiritAgent-${Version}-update.zip"
        if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
        Add-Type -AssemblyName 'System.IO.Compression.FileSystem'
        [IO.Compression.ZipFile]::CreateFromDirectory($stageDir, $zipPath)
        Write-Output "==> Update zip: $zipPath ($((Get-Item $zipPath).Length) bytes)"
    } finally {
        Remove-Item -Recurse -Force $stageDir -ErrorAction SilentlyContinue
    }
}
