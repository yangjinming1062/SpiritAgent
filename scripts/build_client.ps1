# 打包 SpiritAgent 客户端安装器（Windows）。Backend 由 Docker 单独构建，不在此入口内。
# 编排顺序：构建 runner wheel → electron-builder 桌面端 → 暂存 payload → 临时改 tauri.conf.json → Tauri 构建 → 还原配置 → 拷贝最终安装器。
# 用法：powershell -File scripts\build_client.ps1 -Version 0.16.0 [-SkipRunner] [-SkipDesktop] [-SignTool PATH] [-CertThumbprint TH] [-OutputDir DIR]

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [switch]$SkipRunner,
    [switch]$SkipDesktop,

    [string]$SignTool = "signtool.exe",
    [string]$CertThumbprint,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path "$ScriptDir\..").Path
$BuildHelpersPy = Join-Path $ScriptDir "lib\build_helpers.py"

# 加载共享签名 + manifest 助手（Sign-Manifest / New-RunnerManifest / Resolve-UpdateSigningKey）。
. (Join-Path $ScriptDir 'lib/UpdateManifest.ps1')

if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot "release" }

$DesktopPnpmTarget = "dist:win:nsis"
$DesktopArtifactGlob = "SpiritAgent-${Version}-win-*.exe"

function Set-Version([string]$v) {
    & uv run python $BuildHelpersPy set-version $v
    if ($LASTEXITCODE -ne 0) { throw "Set-Version failed" }
}

function Stage-Payload {
    & uv run python $BuildHelpersPy stage-payload --target win
    if ($LASTEXITCODE -ne 0) { throw "Stage-Payload failed" }
}

function Patch-TauriConfig {
    & uv run python $BuildHelpersPy patch-tauri-config
    if ($LASTEXITCODE -ne 0) { throw "Patch-TauriConfig failed" }
}

function Restore-TauriConfig {
    & uv run python $BuildHelpersPy restore-tauri-config
    if ($LASTEXITCODE -ne 0) { throw "Restore-TauriConfig failed" }
}

function Build-UpdateZip {
    # 构造自更新 zip：同时装入桌面端产物 + runner wheel + server.py，一次更新覆盖客户端两侧；
    # wheel 内已带 skills（runner/pyproject.toml 的 package_data），不需要单独的 skills tar。
    # 后端按 zip 内的 `runner/` 布局提取，便于后续一致性校验。
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
        # 仅拷贝当前版本产物与 manifest（剔除 win-unpacked/、构建调试文件、旧构建残留）。
        $versionPrefix = "SpiritAgent-$Version"
        Get-ChildItem -Path $DesktopReleaseDir -File | Where-Object {
            $_.Name -like "$versionPrefix*" -or
            $_.Name -match '^latest.*\.yml$' -or
            $_.Name -eq 'app-update.yml'
        } | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination $stageDir -Force
        }

        # 暂存 runner wheel 与 server.py。
        $runnerStage = Join-Path $stageDir 'runner'
        New-Item -ItemType Directory -Path $runnerStage -Force | Out-Null
        Copy-Item -Force $RunnerWheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath))
        Copy-Item -Force $ServerPyPath (Join-Path $runnerStage 'server.py')

        # 一次性解析私钥（后续各平台重签与 New-RunnerManifest 共用）。
        $keyPath = Resolve-UpdateSigningKey

        # 兜底重签：Build-UpdateZip 是规范的签名点（见 lib/UpdateManifest.ps1::Sign-Manifest），此分支处理跨主机构建时 electron-builder 输出未签名 manifest 的情况。
        foreach ($name in @('latest.yml', 'latest-mac.yml')) {
            $manifestPath = Join-Path $stageDir $name
            if (-not (Test-Path $manifestPath)) { continue }
            $raw = Get-Content -Raw $manifestPath
            if ($raw -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') { continue }
            Write-Output "  re-signing $name (signature was missing)"
            Sign-Manifest -ManifestPath $manifestPath -KeyPath $keyPath
        }

        # 构造并签名 latest-runner.yml：桌面端主进程在重启前读取，先把 wheel + server.py 拉到本地、校验完成才提示用户 "Restart now"。
        New-RunnerManifest `
            -Version $Version `
            -WheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath)) `
            -ServerPyPath (Join-Path $runnerStage 'server.py') `
            -OutDir $stageDir `
            -KeyPath $keyPath | Out-Null

        # 选定规范的 Windows NSIS exe 写入 manifest.json，便于后端 _extract_archive_entries 无歧义定位。
        $desktopExe = Get-ChildItem $stageDir -Filter 'SpiritAgent-*-win-*.exe' -File | Select-Object -First 1
        $manifest = [ordered]@{
            version        = $Version
            desktop_path   = if ($desktopExe) { $desktopExe.Name } else { $null }
            runner_wheel   = "runner/$(Split-Path -Leaf $RunnerWheelPath)"
            server_py      = 'runner/server.py'
            manifests      = @('latest.yml', 'latest-mac.yml', 'latest-runner.yml')
        }
        ($manifest | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $stageDir 'manifest.json') -NoNewline

        # 打包 zip。
        $zipPath = Join-Path $OutputDir "SpiritAgent-${Version}-update.zip"
        if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
        Add-Type -AssemblyName 'System.IO.Compression.FileSystem'
        [IO.Compression.ZipFile]::CreateFromDirectory($stageDir, $zipPath)
        Write-Output "==> Update zip: $zipPath ($((Get-Item $zipPath).Length) bytes)"
    } finally {
        Remove-Item -Recurse -Force $stageDir -ErrorAction SilentlyContinue
    }
}

Push-Location $RepoRoot
try {
    Set-Version $Version

    if (-not $SkipRunner) {
        Write-Output "==> Building runner (uv build wheel → dist\spiritagent-agent-*.whl)"
        Push-Location (Join-Path $RepoRoot "runner")
        try {
            & uv sync --frozen --extra dev
            if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
            & uv build --wheel --out-dir dist
            if ($LASTEXITCODE -ne 0) { throw "uv build failed" }
        } finally { Pop-Location }
    } else {
        Write-Output "==> Skipping runner build (-SkipRunner)"
    }

    if (-not $SkipDesktop) {
        Write-Output "==> Building desktop (electron-builder → release\SpiritAgent-${Version}-win-*-nsis.exe)"
        Push-Location (Join-Path $RepoRoot "client")
        try {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
            & pnpm run $DesktopPnpmTarget
            if ($LASTEXITCODE -ne 0) { throw "pnpm run $DesktopPnpmTarget failed" }
        } finally { Pop-Location }
    } else {
        Write-Output "==> Skipping desktop build (-SkipDesktop)"
    }

    $desktopArtifact = Get-ChildItem -Path (Join-Path $RepoRoot "client\release") -Filter $DesktopArtifactGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $desktopArtifact) {
        throw "no desktop artifact matching '$DesktopArtifactGlob' in client\release\"
    }
    Write-Output "==> Desktop artifact: $($desktopArtifact.FullName)"

    Stage-Payload
    Copy-Item -Force $desktopArtifact.FullName (Join-Path $RepoRoot "installer\payload\client\$($desktopArtifact.Name)")

    if ($CertThumbprint) {
        Write-Output "==> Code-signing $($desktopArtifact.Name)"
        & $SignTool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a /sha1 $CertThumbprint $desktopArtifact.FullName
        if ($LASTEXITCODE -ne 0) { throw "signtool sign failed" }
        Copy-Item -Force $desktopArtifact.FullName (Join-Path $RepoRoot "installer\payload\client\$($desktopArtifact.Name)")
    }

    # 不外层包 NSIS：直接产 SpiritAgent-Setup.exe，单文件安装器形态，双击即出 UI（避免 NSIS 把 .exe 解到 Program Files 后用户再手动跑一遍）。
    Patch-TauriConfig
    try {
        Write-Output "==> Tauri build"
        Push-Location (Join-Path $RepoRoot "installer")
        try {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
            & pnpm run tauri -- build --no-bundle
            if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
        } finally { Pop-Location }
    } finally {
        Restore-TauriConfig
    }

    # 在 target/release 找最终 SpiritAgent-Setup.exe（不走 NSIS 包装）。
    $finalDir = Join-Path $RepoRoot "installer\src-tauri\target\release"
    $finalGlob = "SpiritAgent-Setup.exe"
    $final = Get-ChildItem -Path $finalDir -Filter $finalGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $final) {
        throw "Tauri build did not produce $finalDir\$finalGlob"
    }

    if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null }
    $finalName = "SpiritAgent-Setup-${Version}.exe"
    Copy-Item -Force $final.FullName (Join-Path $OutputDir $finalName)

    # SpiritAgent-Setup.exe 已是自包含：build.rs 把 installer/payload/ 整个打成 zip 嵌入二进制，
    # 运行时由 embedded_payload::ensure_extracted() 解压到 SPIRITAGENT_HOME/bootstrap-payload/payload/。
    # 分发只需单个 exe，无需 release/payload/ 旁路目录。

    Write-Output ""
    Write-Output "==> Final installer: $(Join-Path $OutputDir $finalName) (single-file self-contained)"

    # 同一构建再产一个自更新 zip：已安装客户端从后端拉它自更桌面端 + runner，无需重跑 Tauri 安装器。
    $wheel = Get-ChildItem (Join-Path $RepoRoot 'installer\payload\runner') -Filter '*.whl' | Select-Object -First 1
    if ($wheel) {
        Build-UpdateZip `
            -Version $Version `
            -DesktopReleaseDir (Join-Path $RepoRoot 'client\release') `
            -RunnerWheelPath $wheel.FullName `
            -ServerPyPath (Join-Path $RepoRoot 'installer\payload\runner\server.py') `
            -OutputDir $OutputDir
    }
} finally {
    Pop-Location
}
