import { execFile, spawn } from 'node:child_process'
import path from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const APP_TARGET_PREFIX = 'shell:AppsFolder\\'

export interface InstalledApplication {
  target: string
  name: string
  detail: string
  iconPath: string | null
}

export function isPackagedAppTarget(target: string): boolean {
  return (
    target.startsWith(APP_TARGET_PREFIX) &&
    /^[A-Za-z0-9.-]+_[A-Za-z0-9]+![A-Za-z0-9._-]+$/.test(target.slice(APP_TARGET_PREFIX.length))
  )
}

export async function launchPackagedApplication(target: string): Promise<void> {
  if (!isPackagedAppTarget(target)) {
    throw new Error('无效 Windows 应用。')
  }

  await new Promise<void>((resolve, reject) => {
    // Explorer 仅提交激活请求，退出码不表示应用启动结果；默认显示状态允许前台激活。
    const child = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'explorer.exe'), [target], {
      detached: true,
      stdio: 'ignore'
    })

    child.once('error', reject)
    child.once('spawn', () => {
      child.unref()
      resolve()
    })
  })
}

async function queryApplications(script: string): Promise<InstalledApplication[]> {
  const powershell = path.join(
    process.env.SystemRoot || 'C:\\Windows',
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe'
  )

  const command =
    String.raw`
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Get-Executable([string]$value) {
  $target = [Environment]::ExpandEnvironmentVariables($value).Trim().Trim('"')
  if ($target.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0) { return }
  if ([IO.Path]::GetExtension($target) -eq '.exe' -and (Test-Path -LiteralPath $target -PathType Leaf)) {
    Get-Item -LiteralPath $target
  }
}
` + script

  const { stdout } = await execFileAsync(
    powershell,
    ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')],
    { windowsHide: true, timeout: 15_000, maxBuffer: 2 * 1024 * 1024, encoding: 'utf8' }
  )

  const raw: unknown = JSON.parse(stdout.replace(/^\uFEFF/, ''))

  if (!Array.isArray(raw)) {
    throw new Error('Windows 应用目录返回了无效数据。')
  }

  return raw.filter((item: unknown): item is InstalledApplication => {
    if (!item || typeof item !== 'object') {
      return false
    }

    const value = item as Partial<InstalledApplication>

    return (
      typeof value.target === 'string' &&
      (isPackagedAppTarget(value.target) ||
        (path.isAbsolute(value.target) && path.extname(value.target).toLowerCase() === '.exe')) &&
      typeof value.name === 'string' &&
      Boolean(value.name.trim()) &&
      typeof value.detail === 'string' &&
      (value.iconPath === null || (typeof value.iconPath === 'string' && path.isAbsolute(value.iconPath)))
    )
  })
}

export function readRegisteredApplications(): Promise<InstalledApplication[]> {
  return queryApplications(String.raw`
$roots = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths',
  'HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths'
)
$items = @(foreach ($root in $roots) {
  if (!(Test-Path -LiteralPath $root)) { continue }
  foreach ($key in Get-ChildItem -LiteralPath $root) {
    $file = Get-Executable ([string]$key.GetValue(''))
    if ($null -eq $file) { continue }
    $name = $file.VersionInfo.FileDescription
    if (!$name) { $name = $file.VersionInfo.ProductName }
    if (!$name) { $name = $file.BaseName }
    [pscustomobject]@{ target = $file.FullName; name = $name; detail = $file.Name; iconPath = $null }
  }
})
ConvertTo-Json -InputObject $items -Depth 3 -Compress
`)
}

export function readShellApplications(): Promise<InstalledApplication[]> {
  return queryApplications(String.raw`
$folder = (New-Object -ComObject Shell.Application).Namespace('shell:AppsFolder')
if ($null -eq $folder) { throw 'Windows AppsFolder is unavailable.' }
$packages = @{}
Get-AppxPackage | ForEach-Object { $packages[$_.PackageFamilyName] = $_ }
$manifests = @{}
$items = @(foreach ($item in $folder.Items()) {
  $appId = [string]$item.Path
  if ($appId -match '^[A-Za-z0-9.-]+_[A-Za-z0-9]+![A-Za-z0-9._-]+$') {
    $family, $id = $appId.Split('!', 2)
    $package = $packages[$family]
    if ($null -eq $package) { continue }
    if (!$manifests.ContainsKey($family)) { $manifests[$family] = Get-AppxPackageManifest -Package $package.PackageFullName }
    $application = $manifests[$family].Package.Applications.Application | Where-Object { $_.Id -eq $id } | Select-Object -First 1
    if ($null -eq $application -or $application.VisualElements.AppListEntry -eq 'none') { continue }
    $icon = $null
    foreach ($relative in @($application.VisualElements.Square44x44Logo, $application.Executable)) {
      if (!$relative) { continue }
      $candidate = Join-Path -Path $package.InstallLocation -ChildPath $relative
      if (Test-Path -LiteralPath $candidate -PathType Leaf) { $icon = $candidate; break }
    }
    [pscustomobject]@{ target = 'shell:AppsFolder\' + $appId; name = [string]$item.Name; detail = [string]$package.Name; iconPath = $icon }
  } else {
    $file = Get-Executable ([string]$item.ExtendedProperty('System.Link.TargetParsingPath'))
    if ($null -eq $file) { continue }
    [pscustomobject]@{ target = $file.FullName; name = [string]$item.Name; detail = $file.Name; iconPath = $null }
  }
})
ConvertTo-Json -InputObject $items -Depth 3 -Compress
`)
}
