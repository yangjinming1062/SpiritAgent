# 唤生 安装脚本（Windows/PowerShell 5.1+）。协议：-Manifest 输出阶段列表；-Stage NAME 执行单阶段。payload 经 SPIRITAGENT_BUNDLED_* 传入。

[CmdletBinding()]
param(
    [switch]$Manifest,
    [string]$Stage
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$InformationPreference = 'Continue'
$ErrorActionPreference = "Stop"
$ProtocolVersion = 2
$PythonVersion = "3.13"
$PythonFallbackVersions = @("3.14")

# 环境变量 > 默认值（与 install.sh 一致）
if (-not $env:SPIRITAGENT_HOME) { $env:SPIRITAGENT_HOME = Join-Path $env:LOCALAPPDATA "SpiritAgent" }
$SpiritAgentHome = $env:SPIRITAGENT_HOME
$BundledRunnerDir = $env:SPIRITAGENT_BUNDLED_RUNNER_DIR
$BundledDesktopDir = $env:SPIRITAGENT_BUNDLED_DESKTOP_DIR
$BundledSkillsDir = $env:SPIRITAGENT_BUNDLED_SKILLS_DIR
$BundledOnboardingAudioDir = $env:SPIRITAGENT_BUNDLED_ONBOARDING_AUDIO_DIR
$InstallerFormat = if ($env:SPIRITAGENT_INSTALLER_FORMAT) { $env:SPIRITAGENT_INSTALLER_FORMAT } else { "nsis" }

function Emit-Manifest {
    Write-Output "__SPIRITAGENT_MANIFEST__:{`"protocol_version`": $ProtocolVersion, `"stages`": [{`"name`": `"welcome`", `"title`": `"\u51c6\u5907\u5b89\u88c5`"}, {`"name`": `"install-python`", `"title`": `"\u51c6\u5907\u8fd0\u884c\u73af\u5883`"}, {`"name`": `"unpack-runner`", `"title`": `"\u5b89\u88c5\u7535\u8111\u52a9\u624b`"}, {`"name`": `"unpack-desktop`", `"title`": `"\u5b89\u88c5\u5524\u751f\u5e94\u7528`"}, {`"name`": `"install-skills`", `"title`": `"\u51c6\u5907\u5e38\u7528\u529f\u80fd`"}, {`"name`": `"finalize`", `"title`": `"\u5b8c\u6210\u5b89\u88c5\u8bbe\u7f6e`"}]}"
}

function Install-Uv {
    $managedUv = Join-Path $SpiritAgentHome "bin\uv.exe"
    if (Test-Path $managedUv) {
        $script:UvCmd = $managedUv
        return $true
    }

    $binDir = Join-Path $SpiritAgentHome "bin"
    if (-not (Test-Path $binDir)) {
        New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    }

    try {
        $env:UV_INSTALL_DIR = $binDir
        $psHostExe = Get-PowerShellHostExe
        & $psHostExe -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null

        if (Test-Path $managedUv) {
            $script:UvCmd = $managedUv
            return $true
        }
        return $false
    } catch {
        return $false
    }
}

function Install-OfficeCli {
    $existing = Get-Command "officecli" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $managedOfficeCli = Join-Path $SpiritAgentHome "bin\officecli.exe"
    if ($existing -or (Test-Path $managedOfficeCli)) {
        return
    }

    try {
        $psHostExe = Get-PowerShellHostExe
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $psHostExe -ExecutionPolicy ByPass -c "irm https://d.officecli.ai/install.ps1 | iex" 2>&1 | Out-Null
        } finally {
            # 下载失败也须恢复 EAP=Stop，否则后续阶段失败被静默吞掉
            $ErrorActionPreference = $prevEAP
        }

        $defaultInstallExe = Join-Path $env:LOCALAPPDATA "OfficeCLI\officecli.exe"
        if (Test-Path $defaultInstallExe) {
            $binDir = Join-Path $SpiritAgentHome "bin"
            if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Force -Path $binDir | Out-Null }
            Copy-Item -Force $defaultInstallExe (Join-Path $binDir "officecli.exe")
        }
    } catch { }
}

function Get-PowerShellHostExe {
    try {
        $hostExe = (Get-Process -Id $PID).Path
        if ($hostExe -and (Test-Path $hostExe)) {
            $leaf = Split-Path $hostExe -Leaf
            if ($leaf -match '^(?i:powershell|pwsh)\.exe$') { return $hostExe }
        }
    } catch { }
    foreach ($candidate in @("powershell", "pwsh")) {
        $cmd = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($cmd -and $cmd.Source) { return $cmd.Source }
    }
    return "powershell"
}

function Test-Python {
    if (-not $script:UvCmd) {
        if (-not (Install-Uv)) { return $false }
    }

    foreach ($ver in (@($PythonVersion) + $PythonFallbackVersions)) {
        try {
            $found = & $script:UvCmd python find $ver 2>$null
            if ($found) {
                $script:PythonVersion = $ver
                return $true
            }
        } catch { }
    }

    # 冷缓存：安装首选版本后只再查该版本
    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $script:UvCmd python install $PythonVersion 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        $found = & $script:UvCmd python find $PythonVersion 2>$null
        if ($found) {
            return $true
        }
    } catch {
        $ErrorActionPreference = $prevEAP
    }

    return $false
}

# EAP=Stop 下 Write-Error 会抛异常吞掉退出码；错误帧统一走 stderr + 显式退出码
function Fail([string]$reason) {
    [Console]::Error.WriteLine($reason)
    return 1
}

# 阶段 1：welcome
function Stage-Welcome {
    foreach ($d in @("$SpiritAgentHome\bin", "$SpiritAgentHome\skills", "$SpiritAgentHome\logs")) {
        if (-not (Test-Path $d)) {
            New-Item -ItemType Directory -Force -Path $d | Out-Null
        }
    }
    if (-not (Test-Path $SpiritAgentHome -PathType Container)) {
        return Fail "could not create SPIRITAGENT_HOME: $SpiritAgentHome"
    }
    return 0
}

# 阶段 2：安装 Python
function Stage-InstallPython {
    if (Test-Python) { return 0 }
    return Fail "Python $PythonVersion is required but could not be installed. Install Python manually from https://www.python.org/downloads/ and re-run."
}

# 阶段 3：解包运行器
function Stage-UnpackRunner {
    if (-not $script:UvCmd -or -not $script:PythonVersion) {
        if (-not (Test-Python)) {
            return Fail "Python runtime not available (uv or Python missing)"
        }
    }

    if (-not $BundledRunnerDir) {
        return Fail "SPIRITAGENT_BUNDLED_RUNNER_DIR is required"
    }
    if (-not (Test-Path $BundledRunnerDir -PathType Container)) {
        return Fail "bundled runner dir not found: $BundledRunnerDir"
    }

    $wheels = @(Get-ChildItem -Path $BundledRunnerDir -Filter "spirit_agent-*.whl" -File -ErrorAction SilentlyContinue)
    if ($wheels.Count -ne 1) {
        return Fail "expected one runner wheel in $BundledRunnerDir, found $($wheels.Count)"
    }
    $wheel = $wheels[0]

    $venvDir = Join-Path $SpiritAgentHome "runner\.venv"
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        # 创建 venv（依赖 install-python 完成）；--clear 重建，避免遗留损坏环境
        $venvOutput = & $script:UvCmd venv $venvDir --python $script:PythonVersion --clear 2>&1
        if ($LASTEXITCODE) {
            return Fail "uv venv failed: $($venvOutput -join ' | ')"
        }

        # 安装 wheel（含依赖一次性装入 venv）
        $pythonExe = Join-Path $venvDir "Scripts\python.exe"
        $pipOutput = & $script:UvCmd pip install --python $pythonExe $wheel.FullName 2>&1
        if ($LASTEXITCODE) {
            # 网络不稳时回退国内镜像
            $pypiIndex = $env:SPIRITAGENT_PYPI_INDEX_URL
            if (-not $pypiIndex) { $pypiIndex = $env:PIP_INDEX_URL }
            if (-not $pypiIndex) { $pypiIndex = "https://mirrors.aliyun.com/pypi/simple/" }
            $pipOutputRetry = & $script:UvCmd pip install --python $pythonExe $wheel.FullName --index-url $pypiIndex 2>&1
            if ($LASTEXITCODE) {
                return Fail "uv pip install failed: $($pipOutputRetry -join ' | ')"
            }
        }

        $checkOutput = & $pythonExe -I -m spirit_agent_runner --check 2>&1
        if ($LASTEXITCODE) {
            return Fail "runner health check failed: $($checkOutput -join ' | ')"
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    # onboarding 音频：语言子目录 1:1 映射至 audio/onboarding/<lang>/
    if ($BundledOnboardingAudioDir -and (Test-Path $BundledOnboardingAudioDir -PathType Container)) {
        Get-ChildItem -Path $BundledOnboardingAudioDir -Directory | ForEach-Object {
            $audioTarget = Join-Path $SpiritAgentHome "audio\onboarding\$($_.Name)"
            if (-not (Test-Path $audioTarget)) { New-Item -ItemType Directory -Force -Path $audioTarget | Out-Null }
            Copy-Item -Recurse -Force (Join-Path $_.FullName "*") $audioTarget
        }
    }

    return 0
}

# 阶段 4：解包桌面端
function Stage-UnpackDesktop {
    if (-not $BundledDesktopDir) {
        return Fail "SPIRITAGENT_BUNDLED_DESKTOP_DIR is required"
    }
    if (-not (Test-Path $BundledDesktopDir -PathType Container)) {
        return Fail "bundled desktop dir not found: $BundledDesktopDir"
    }

    switch ($InstallerFormat) {
        "nsis" { $pattern = "*.exe" }
        "zip"  { $pattern = "*.zip" }
        default {
            return Fail "unknown desktop format: $InstallerFormat"
        }
    }
    $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter $pattern -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $artifact) {
        return Fail "no desktop artifact found in $BundledDesktopDir (format=$InstallerFormat)"
    }

    if ($InstallerFormat -eq "nsis") {
        # NSIS /S 静默安装，/D 指定目录；已有可见窗口，子进程不再开控制台
        $installDir = Join-Path $env:LOCALAPPDATA "Programs\SpiritAgent"
        if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Force -Path $installDir | Out-Null }
        $proc = Start-Process -FilePath $artifact.FullName -ArgumentList @("/S", "/D=$installDir") -Wait -NoNewWindow -PassThru
        if ($proc.ExitCode -ne 0) {
            return Fail "NSIS installer exited with code $($proc.ExitCode)"
        }
    } else {
        $installDir = Join-Path $SpiritAgentHome "apps\SpiritAgent"
        if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
        Expand-Archive -Path $artifact.FullName -DestinationPath $installDir -Force
    }
    return 0
}

# 阶段 5：安装技能
function Stage-InstallSkills {
    if (-not $BundledSkillsDir) {
        return Fail "SPIRITAGENT_BUNDLED_SKILLS_DIR is required"
    }
    if (-not (Test-Path $BundledSkillsDir -PathType Container)) {
        return Fail "bundled skills dir not found: $BundledSkillsDir"
    }

    # 尊重 .no-bundled-skills 标记
    if (Test-Path (Join-Path $SpiritAgentHome ".no-bundled-skills")) {
        return 0
    }

    $skillsDir = Join-Path $SpiritAgentHome "skills"
    if (-not (Test-Path $skillsDir)) { New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null }

    # /E 只增不删，保留用户自装技能；退出码 0-7 成功，>=8 失败
    & robocopy $BundledSkillsDir $skillsDir /E /NFL /NDL /NJH /NJS /NP /R:0 /W:0 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        return Fail "robocopy skills failed: exit $LASTEXITCODE"
    }

    # OfficeCLI 尽力安装
    Install-OfficeCli | Out-Null
    return 0
}

# 阶段 6：收尾
function Stage-Finalize {
    Set-Content -Path (Join-Path $SpiritAgentHome ".spiritagent-bootstrap-complete") -Value "" -NoNewline
    return 0
}

if ($Manifest) {
    Emit-Manifest
    exit 0
}

if (-not $Stage) {
    [Console]::Error.WriteLine("error: -Stage NAME is required (or pass -Manifest)")
    exit 2
}

# 阶段函数返回值即退出码；exit 落 stderr 供 Tauri 判失败原因。
switch ($Stage) {
    "welcome"        { exit (Stage-Welcome) }
    "install-python" { exit (Stage-InstallPython) }
    "unpack-runner"  { exit (Stage-UnpackRunner) }
    "unpack-desktop" { exit (Stage-UnpackDesktop) }
    "install-skills" { exit (Stage-InstallSkills) }
    "finalize"       { exit (Stage-Finalize) }
    default {
        [Console]::Error.WriteLine("unknown stage: $Stage")
        exit 1
    }
}
