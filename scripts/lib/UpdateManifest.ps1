# scripts/lib/UpdateManifest.ps1 —— SpiritAgent 自更新管线的共享签名与 manifest 助手；由 scripts/build_client.ps1 的 Build-UpdateZip 引入。
# 仅做纯文件操作，没有模块级可变状态。

# 定位 openssl.exe：Git for Windows 把 openssl 放在 mingw64/bin 下，非 git 环境下不在 PATH 中，故多走几步常见安装位置查找。
function Resolve-OpenSsl {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    # 兜底：常见 Git 安装位置
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

# Resolve-UpdateSigningKey — locates the PEM private key used to sign release manifests.
# The key MUST live outside the repo: an env var points to it, otherwise the well-known
# per-user default is used. An earlier revision stored the key under scripts/secrets/
# in the repo itself, which forced a wholesale keypair rotation and history rewrite
# (see docs/SECURITY.md).
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

# 就地签署 Squirrel 风格 manifest：根据顶层 `path` 字段计算 SHA-512（大写），用 openssl 对 "<path>|<sha512>" 签出 `signature`，并同步回写 `sha512` 与 `files[]`。
# 缺少 `path` 字段、引用的文件不存在、或 openssl 不在 PATH 时抛错。
function Sign-Manifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$KeyPath,
        # 调用方已算好的 SHA-512/大小（如构建新 manifest 时），传入可避免对几百 MB 的 wheel 重复哈希。
        [string]$Sha512 = '',
        [long]$Size = -1
    )

    if (-not (Test-Path $ManifestPath)) {
        Write-Warning "Manifest $ManifestPath does not exist; skipping"
        return
    }

    $manifestDir = Split-Path -Parent $ManifestPath
    $raw = Get-Content $ManifestPath -Raw

    # 用正则提取 `path`，同时支持 JSON 与 YAML，避免引入 YAML 解析器。
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

    # 用临时文件落地签名输出：PowerShell `&` 操作符把二进制 stdout 当数组收，无法直接成字节流。
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

    # 同样用正则就地改字段，兼容 YAML 与 JSON 两种 manifest。
    $updated = $raw

    # sha512:  sha512: <value>  or  "sha512": "<value>"
    $updated = $updated -replace '(?m)(\s*"?\s*sha512\s*"?\s*[:=]\s*"?\s*)[^\r\n"]*', "`${1}$sha512"

    # files 数组：整段替换为单元素数组（同时覆盖 YAML 与 JSON 形态）。
    $filesBlockYaml = "  - url: $pathValue`n    sha512: $sha512`n    size: $size"
    $filesBlockJson = "`"files`": [{`"url`": `"$pathValue`", `"sha512`": `"$sha512`", `"size`": $size}]"
    if ($updated -match '(?m)^\s*"?\s*files\s*"?\s*:\s*\[') {
        $updated = $updated -replace '(?m)(\s*"?\s*files\s*"?\s*:\s*)\[.*?\]', "`${1}$filesBlockJson"
    } else {
        $updated = $updated -replace '(?s)(files:\s*\n)(\s+-[\s\S]*?)(?=\n\S|\n\n|\z)', "`${1}$filesBlockYaml"
    }

    # signature: add or replace
    if ($updated -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') {
        $updated = $updated -replace '(?m)(\s*"?\s*signature\s*"?\s*[:=]\s*"?\s*)[^\r\n"]*', "`${1}$signatureB64"
    } else {
        $updated = $updated.TrimEnd() + "`nsignature: $signatureB64`n"
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ManifestPath, $updated, $utf8NoBom)
}

# 为 runner wheel + server.py 构造并签出 `latest-runner.yml`：顶层 Squirrel 字段指向 wheel，`runner` 块记录 wheel/server.py 哈希，`signature` 用桌面端同一密钥对签出。返回签名后 YAML 的绝对路径。
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

    # Squirrel 约定 `path` 相对 manifest 所在目录；构建布局把 wheel 暂存至 <staging>/runner/，故 manifest 写在 staging 根，path 为 runner/<wheel>。
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

    # 把上面已算好的 SHA-512/大小直接传下去，避免 Sign-Manifest 对几百 MB 的 wheel 重哈希。
    Sign-Manifest -ManifestPath $manifestPath -KeyPath $KeyPath -Sha512 $wheelSha -Size $wheelSize

    return $manifestPath
}
