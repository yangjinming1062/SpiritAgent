# 唤生 安装脚本（Windows / PowerShell 5.1+）。由 Tauri SpiritAgent-Setup.exe 调用；
# 6 阶段负载释放：安装 Python（如需）、拷贝 runner wheel / 桌面安装器 / skills 至 $SPIRITAGENT_HOME 及平台规范位置。
# 协议：
#   powershell -File install.ps1 -Manifest                 → 输出 manifest JSON
#   powershell -File install.ps1 -Stage NAME -Json         → 执行单个阶段，输出结果帧
# payload 位置通过 SPIRITAGENT_BUNDLE_* 环境变量或对应 --bundled-*-dir 参数传递；二者并存时环境变量优先。

[CmdletBinding()]
param(
    [switch]$Manifest,
    [string]$Stage,
    [switch]$Json,
    [switch]$NonInteractive,
    [string]$SpiritAgentHome,
    [string]$BundledRunnerDir,
    [string]$BundledDesktopDir,
    [string]$BundledSkillsDir,
    [string]$BundledOnboardingAudioDir,
    [string]$InstallerFormat
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$InformationPreference = 'Continue'
$ErrorActionPreference = "Stop"
$ProtocolVersion = 2
$ScriptName = "install.ps1"
$RunnerWheelGlob = "spirit_agent-*.whl"
$DefaultDesktopFormat = "nsis"
$PythonVersion = "3.13"
$PythonFallbackVersions = @("3.12", "3.14", "3.11")

# 路径优先级：环境变量 > 参数 > 默认值
if (-not $SpiritAgentHome) {
    if ($env:SPIRITAGENT_HOME) { $SpiritAgentHome = $env:SPIRITAGENT_HOME }
    else { $SpiritAgentHome = Join-Path $env:LOCALAPPDATA "SpiritAgent" }
}
if (-not $BundledRunnerDir -and $env:SPIRITAGENT_BUNDLED_RUNNER_DIR) { $BundledRunnerDir = $env:SPIRITAGENT_BUNDLED_RUNNER_DIR }
if (-not $BundledDesktopDir -and $env:SPIRITAGENT_BUNDLED_DESKTOP_DIR) { $BundledDesktopDir = $env:SPIRITAGENT_BUNDLED_DESKTOP_DIR }
if (-not $BundledSkillsDir -and $env:SPIRITAGENT_BUNDLED_SKILLS_DIR) { $BundledSkillsDir = $env:SPIRITAGENT_BUNDLED_SKILLS_DIR }
if (-not $BundledOnboardingAudioDir -and $env:SPIRITAGENT_BUNDLED_ONBOARDING_AUDIO_DIR) { $BundledOnboardingAudioDir = $env:SPIRITAGENT_BUNDLED_ONBOARDING_AUDIO_DIR }
if (-not $InstallerFormat) {
    if ($env:SPIRITAGENT_INSTALLER_FORMAT) { $InstallerFormat = $env:SPIRITAGENT_INSTALLER_FORMAT }
    else { $InstallerFormat = $DefaultDesktopFormat }
}

function Emit-Manifest {
    Write-Output "__SPIRITAGENT_MANIFEST__:{`"protocol_version`": $ProtocolVersion, `"stages`": [{`"name`": `"welcome`", `"title`": `"\u51c6\u5907\u5b89\u88c5`", `"category`": `"setup`", `"needs_user_input`": false}, {`"name`": `"install-python`", `"title`": `"\u5b89\u88c5 Python \u8fd0\u884c\u65f6`", `"category`": `"prereqs`", `"needs_user_input`": false}, {`"name`": `"unpack-runner`", `"title`": `"\u5b89\u88c5 \u5524\u751f \u8fd0\u884c\u5668`", `"category`": `"payload`", `"needs_user_input`": false}, {`"name`": `"unpack-desktop`", `"title`": `"\u5b89\u88c5 \u5524\u751f \u684c\u9762\u5e94\u7528`", `"category`": `"payload`", `"needs_user_input`": false}, {`"name`": `"install-skills`", `"title`": `"\u5b89\u88c5\u5185\u7f6e\u6280\u80fd`", `"category`": `"payload`", `"needs_user_input`": false}, {`"name`": `"finalize`", `"title`": `"\u5b8c\u6210\u5b89\u88c5`", `"category`": `"finalize`", `"needs_user_input`": false}]}"
}

function Escape-JsonString([string]$s) {
    if ($null -eq $s) { return "" }
    return $s.Replace('\', '\\').Replace('"', '\"').Replace("`n", '\n').Replace("`r", '\r').Replace("`t", '\t')
}

function Emit-StageOk([string]$stage, [bool]$skipped = $false, [string]$reason = "") {
    if ($skipped -and $reason) {
        Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"$stage`", `"skipped`": true, `"reason`": `"" + (Escape-JsonString $reason) + "`"}"
    } else {
        Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"$stage`"}"
    }
}

function Emit-StageErr([string]$stage, [string]$reason) {
    Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": false, `"stage`": `"$stage`", `"reason`": `"" + (Escape-JsonString $reason) + "`"}"
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

        if (-not (Test-Path $managedUv)) {
            return $false
        }
        $script:UvCmd = $managedUv
        return $true
    } catch {
        return $false
    }
}

function Install-OfficeCli {
    $existing = Get-Command "officecli" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $managedOfficeCli = Join-Path $SpiritAgentHome "bin\officecli.exe"
    if ($existing -or (Test-Path $managedOfficeCli)) {
        return $true
    }

    try {
        $psHostExe = Get-PowerShellHostExe
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $psHostExe -ExecutionPolicy ByPass -c "irm https://d.officecli.ai/install.ps1 | iex" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        $defaultInstallExe = Join-Path $env:LOCALAPPDATA "OfficeCLI\officecli.exe"
        if (Test-Path $defaultInstallExe) {
            $binDir = Join-Path $SpiritAgentHome "bin"
            if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Force -Path $binDir | Out-Null }
            Copy-Item -Force $defaultInstallExe (Join-Path $binDir "officecli.exe")
            return $true
        }
        return (Test-Path $managedOfficeCli)
    } catch {
        return $false
    }
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

    $candidates = @($PythonVersion) + $PythonFallbackVersions
    foreach ($ver in $candidates) {
        if (-not $ver) { continue }
        try {
            $found = & $script:UvCmd python find $ver 2>$null
            if ($found) {
                $script:PythonVersion = $ver
                return $true
            }
        } catch { }
    }

    # 冷缓存：安装首选版本后再次只查该版本。
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

# 阶段 1：welcome
function Stage-Welcome {
    $bin = Join-Path $SpiritAgentHome "bin"
    $skills = Join-Path $SpiritAgentHome "skills"
    $logs = Join-Path $SpiritAgentHome "logs"

    foreach ($d in @($bin, $skills, $logs)) {
        if (-not (Test-Path $d)) {
            New-Item -ItemType Directory -Force -Path $d | Out-Null
        }
    }

    if (-not (Test-Path $SpiritAgentHome -PathType Container)) {
        Emit-StageErr "welcome" "could not create SPIRITAGENT_HOME: $SpiritAgentHome"
        return 1
    }

    $marker = Join-Path $SpiritAgentHome ".spiritagent-bootstrap-complete"
    $isReinstall = Test-Path $marker

    $escHome = Escape-JsonString $SpiritAgentHome
    Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"welcome`", `"data`": {`"spiritagent_home`": `"$escHome`", `"is_reinstall`": $($isReinstall.ToString().ToLower())}}"
    return 0
}

# 阶段 2：安装 Python
function Stage-InstallPython {
    if (Test-Python) {
        $escVer = Escape-JsonString $script:PythonVersion
        Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"install-python`", `"data`": {`"version`": `"$escVer`"}}"
        return 0
    }

    Emit-StageErr "install-python" "Python $PythonVersion is required but could not be installed. Install Python manually from https://www.python.org/downloads/ and re-run."
    return 1
}

# 阶段 3：解包运行器
function Stage-UnpackRunner {
    if (-not $script:UvCmd -or -not $script:PythonVersion) {
        if (-not (Test-Python)) {
            Emit-StageErr "unpack-runner" "Python runtime not available (uv or Python missing)"
            return 1
        }
    }

    if (-not $BundledRunnerDir) {
        Emit-StageErr "unpack-runner" "--BundledRunnerDir (or SPIRITAGENT_BUNDLED_RUNNER_DIR) is required"
        return 1
    }
    if (-not (Test-Path $BundledRunnerDir -PathType Container)) {
        Emit-StageErr "unpack-runner" "bundled runner dir not found: $BundledRunnerDir"
        return 1
    }

    $wheel = Get-ChildItem -Path $BundledRunnerDir -Filter $RunnerWheelGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $wheel) {
        Emit-StageErr "unpack-runner" "wheel not found in $BundledRunnerDir"
        return 1
    }

    $runnerDir = Join-Path $SpiritAgentHome "runner"
    if (-not (Test-Path $runnerDir)) { New-Item -ItemType Directory -Force -Path $runnerDir | Out-Null }

    # 拷贝 server.py 与 wheel 同级（不进入 wheel）
    $serverSrc = Join-Path $BundledRunnerDir "server.py"
    if (Test-Path $serverSrc) { Copy-Item -Force $serverSrc (Join-Path $runnerDir "server.py") }

    # 创建 venv（依赖 install-python 阶段完成）
    $venvDir = Join-Path $runnerDir ".venv"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $venvOutput = & $script:UvCmd venv $venvDir --python $script:PythonVersion --clear 2>&1
    if ($LASTEXITCODE) { $ErrorActionPreference = $prevEAP; Emit-StageErr "unpack-runner" "uv venv failed: $($venvOutput -join ' | ')"; return 1 }

    # 安装 wheel 至 venv（含依赖一次性安装）
    $pythonExe = Join-Path $venvDir "Scripts\python.exe"
    $pipOutput = & $script:UvCmd pip install --python $pythonExe $wheel.FullName 2>&1
    if ($LASTEXITCODE) {
        # 网络不稳时回退至国内镜像
        $pypiIndex = $env:SPIRITAGENT_PYPI_INDEX_URL
        if (-not $pypiIndex) { $pypiIndex = $env:PIP_INDEX_URL }
        if (-not $pypiIndex) { $pypiIndex = "https://mirrors.aliyun.com/pypi/simple/" }
        $pipOutputRetry = & $script:UvCmd pip install --python $pythonExe $wheel.FullName --index-url $pypiIndex 2>&1
        if ($LASTEXITCODE) {
            $ErrorActionPreference = $prevEAP; Emit-StageErr "unpack-runner" "uv pip install failed: $($pipOutputRetry -join ' | ')"; return 1
        }
    }

    # 不做安装后烟测：build_client.ps1 已通过 pytest tests/test_startup_imports.py 把 wheel 卡在打包前，损坏 venv 不会到达用户。

    # 清理旧的 PyInstaller 二进制
    $oldBin = Join-Path (Join-Path $SpiritAgentHome "bin") "spiritagent-runner.exe"
    if (Test-Path $oldBin) { Remove-Item -Force $oldBin }

    # 拷贝 onboarding 引导音频：语言子目录（zh\、en\、…）1:1 映射至 $SpiritAgentHome\audio\onboarding\<lang>\。
    $audioCount = 0
    if ($BundledOnboardingAudioDir -and (Test-Path $BundledOnboardingAudioDir -PathType Container)) {
        Get-ChildItem -Path $BundledOnboardingAudioDir -Directory | ForEach-Object {
            $audioTarget = Join-Path (Join-Path (Join-Path $SpiritAgentHome "audio") "onboarding") $_.Name
            if (-not (Test-Path $audioTarget)) { New-Item -ItemType Directory -Force -Path $audioTarget | Out-Null }
            Copy-Item -Recurse -Force (Join-Path $_.FullName "*") $audioTarget
        }
        $audioRoot = Join-Path (Join-Path $SpiritAgentHome "audio") "onboarding"
        $audioCount = (Get-ChildItem -Path $audioRoot -Filter "*.mp3" -Recurse -File -ErrorAction SilentlyContinue).Count
    }

    $size = $wheel.Length
    $escVenv = Escape-JsonString $venvDir
    $escWheel = Escape-JsonString $wheel.Name
    Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"unpack-runner`", `"data`": {`"venv`": `"$escVenv`", `"wheel`": `"$escWheel`", `"size_bytes`": $size, `"onboarding_audio_copied`": $audioCount}}"
    return 0
}

# 阶段 4：解包桌面端
function Stage-UnpackDesktop {
    if (-not $BundledDesktopDir) {
        Emit-StageErr "unpack-desktop" "--BundledDesktopDir (or SPIRITAGENT_BUNDLED_DESKTOP_DIR) is required"
        return 1
    }
    if (-not (Test-Path $BundledDesktopDir -PathType Container)) {
        Emit-StageErr "unpack-desktop" "bundled desktop dir not found: $BundledDesktopDir"
        return 1
    }

    # 按格式定位产物
    $artifact = $null
    switch ($InstallerFormat) {
        "nsis" { $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter "*.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
        "msi"  { $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter "*.msi"  -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
        "zip"  { $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter "*.zip"  -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
        default {
            Emit-StageErr "unpack-desktop" "unknown desktop format: $InstallerFormat"
            return 1
        }
    }

    if (-not $artifact) {
        Emit-StageErr "unpack-desktop" "no desktop artifact found in $BundledDesktopDir (format=$InstallerFormat)"
        return 1
    }
    $artifactPath = $artifact.FullName

    switch ($InstallerFormat) {
        "nsis" {
            # NSIS /S 静默安装，/D 指定安装目录；Tauri 安装器已持有可见窗口，子进程无需再开控制台。
            $localPrograms = Join-Path $env:LOCALAPPDATA "Programs"
            $installDir = Join-Path $localPrograms "SpiritAgent"
            if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Force -Path $installDir | Out-Null }
            $proc = Start-Process -FilePath $artifactPath `
                                  -ArgumentList @("/S", "/D=$installDir") `
                                  -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -ne 0) {
                Emit-StageErr "unpack-desktop" "NSIS installer exited with code $($proc.ExitCode)"
                return 1
            }
            $escPath = Escape-JsonString $installDir
            Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"unpack-desktop`", `"data`": {`"installed_path`": `"$escPath`", `"format`": `"nsis`"}}"
            return 0
        }
        "msi" {
            # msiexec /qn 静默安装，无 UI；REBOOT=ReallySuppress 抑制重启提示。
            $proc = Start-Process -FilePath "msiexec.exe" `
                                  -ArgumentList @("/i", $artifactPath, "/qn", "REBOOT=ReallySuppress") `
                                  -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -ne 0) {
                Emit-StageErr "unpack-desktop" "msiexec exited with code $($proc.ExitCode)"
                return 1
            }
            $localPrograms = Join-Path $env:LOCALAPPDATA "Programs"
            $installDir = Join-Path $localPrograms "SpiritAgent"
            $escPath = Escape-JsonString $installDir
            Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"unpack-desktop`", `"data`": {`"installed_path`": `"$escPath`", `"format`": `"msi`"}}"
            return 0
        }
        "zip" {
            # 解压 ZIP 至 $SPIRITAGENT_HOME\apps\SpiritAgent（与 NSIS 布局一致），后续可从内部的 SpiritAgent.exe 启动；
            # 与 bootstrap.rs::resolve_spiritagent_desktop_exe 在 Windows 上期望的 install_root 路径对齐。
            $dest = Join-Path $SpiritAgentHome "apps\SpiritAgent"
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Expand-Archive -Path $artifactPath -DestinationPath $dest -Force
            $escPath = Escape-JsonString $dest
            Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"unpack-desktop`", `"data`": {`"installed_path`": `"$escPath`", `"format`": `"zip`"}}"
            return 0
        }
    }
    return 1
}

# 阶段 5：安装技能
function Stage-InstallSkills {
    if (-not $BundledSkillsDir) {
        Emit-StageErr "install-skills" "--BundledSkillsDir (or SPIRITAGENT_BUNDLED_SKILLS_DIR) is required"
        return 1
    }
    if (-not (Test-Path $BundledSkillsDir -PathType Container)) {
        Emit-StageErr "install-skills" "bundled skills dir not found: $BundledSkillsDir"
        return 1
    }

    # 尊重 .no-bundled-skills 标记
    $noSkillsMarker = Join-Path $SpiritAgentHome ".no-bundled-skills"
    if (Test-Path $noSkillsMarker) {
        Emit-StageOk "install-skills" $true "user opted out via .no-bundled-skills"
        return 0
    }

    $skillsDir = Join-Path $SpiritAgentHome "skills"
    if (-not (Test-Path $skillsDir)) { New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null }

    # robocopy /MIR 镜像（保留用户未与 bundle 冲突的本地内容）。退出码 0-7 视为成功，>=8 视为失败。
    & robocopy $BundledSkillsDir $skillsDir /MIR /NFL /NDL /NJH /NJS /NP /R:0 /W:0 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Emit-StageErr "install-skills" "robocopy skills failed: exit $LASTEXITCODE"
        return 1
    }

    # 动态安装 OfficeCLI（若网络可用）
    Install-OfficeCli | Out-Null

    $bundledCount = (Get-ChildItem -Path $skillsDir -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"install-skills`", `"data`": {`"bundled_count`": $bundledCount}}"
    return 0
}

# 阶段 6：收尾
function Stage-Finalize {
    $marker = Join-Path $SpiritAgentHome ".spiritagent-bootstrap-complete"
    Set-Content -Path $marker -Value "" -NoNewline

    $escMarker = Escape-JsonString $marker
    Write-Output "__SPIRITAGENT_STAGE_RESULT__:{`"ok`": true, `"stage`": `"finalize`", `"data`": {`"marker`": `"$escMarker`"}}"
    return 0
}

if ($Manifest) {
    Emit-Manifest
    exit 0
}

if (-not $Stage) {
    Write-Error "error: -Stage NAME is required (or pass -Manifest)"
    exit 2
}

# 运行阶段脚本块：通过 Write-Output 输出的 JSON 帧进入 stdout；阶段 `return` 值（结果数组最后一个元素）作为退出码。
function Run-Stage([scriptblock]$fn) {
    $result = @(& $fn)
    $code = if ($result.Count -gt 0) { $result[-1] } else { 0 }
    $result | Select-Object -First ($result.Count - 1) | ForEach-Object { Write-Output $_ }
    if ($code) { exit $code }
}

switch ($Stage) {
    "welcome"        { Run-Stage { Stage-Welcome } }
    "install-python" { Run-Stage { Stage-InstallPython } }
    "unpack-runner"  { Run-Stage { Stage-UnpackRunner } }
    "unpack-desktop" { Run-Stage { Stage-UnpackDesktop } }
    "install-skills" { Run-Stage { Stage-InstallSkills } }
    "finalize"       { Run-Stage { Stage-Finalize } }
    default {
        Emit-StageErr $Stage "unknown stage"
        exit 1
    }
}
