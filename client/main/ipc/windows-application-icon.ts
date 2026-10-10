import { execFile } from 'node:child_process'
import path from 'node:path'
import { promisify } from 'node:util'

import { app, nativeImage, shell } from 'electron'
import log from 'electron-log/main'

const execFileAsync = promisify(execFile)
const ICON_SIZE = 64
const MAX_NATIVE_BATCH = 64

interface IconSource {
  file: string
  index: number
}

interface PendingIcon {
  source: IconSource
  resolvers: ((icon: string | null) => void)[]
}

const pending = new Map<string, PendingIcon>()
let reading = false

async function extractIcons(sources: IconSource[]): Promise<(string | null)[]> {
  const input = Buffer.from(JSON.stringify(sources)).toString('base64')

  const command = String.raw`
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
public static class ApplicationIcons {
  [DllImport("user32.dll", EntryPoint = "PrivateExtractIconsW", CharSet = CharSet.Unicode)]
  private static extern uint Extract(string file, int index, int width, int height,
    out IntPtr icon, out uint id, uint count, uint flags);
  [DllImport("user32.dll")]
  private static extern bool DestroyIcon(IntPtr icon);
  public static string Read(string file, int index) {
    IntPtr handle = IntPtr.Zero;
    uint id;
    try {
      if (Extract(file, index, ${ICON_SIZE}, ${ICON_SIZE}, out handle, out id, 1, 0) != 1 || handle == IntPtr.Zero) return null;
      using (var icon = Icon.FromHandle(handle))
      using (var bitmap = icon.ToBitmap())
      using (var stream = new MemoryStream()) {
        bitmap.Save(stream, ImageFormat.Png);
        return "data:image/png;base64," + Convert.ToBase64String(stream.ToArray());
      }
    } finally {
      if (handle != IntPtr.Zero) DestroyIcon(handle);
    }
  }
}
'@ -ReferencedAssemblies System.Drawing
$sources = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())))
$icons = @(foreach ($source in $sources) {
  try { [ApplicationIcons]::Read([string]$source.file, [int]$source.index) }
  catch { [Console]::Error.WriteLine($_.Exception.Message); $null }
})
ConvertTo-Json -InputObject $icons -Compress
`

  const powershell = path.join(
    process.env.SystemRoot || 'C:\\Windows',
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe'
  )

  const execution = execFileAsync(
    powershell,
    ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')],
    { windowsHide: true, timeout: 15_000, maxBuffer: 2 * 1024 * 1024, encoding: 'utf8' }
  )

  // 输入走 stdin，避免批量长路径超过 Windows 命令行上限；进程失败由 execution 报告。
  execution.child.stdin?.on('error', () => {})
  execution.child.stdin?.end(input)
  const { stdout, stderr } = await execution

  if (stderr.trim()) {
    log.warn('[Dock] native icon extraction:', stderr.trim())
  }

  const icons: unknown = JSON.parse(stdout.replace(/^\uFEFF/, ''))

  if (
    !Array.isArray(icons) ||
    icons.length !== sources.length ||
    icons.some(icon => icon !== null && typeof icon !== 'string')
  ) {
    throw new Error('Windows 图标读取返回了无效数据。')
  }

  return icons
}

async function drainIcons(): Promise<void> {
  while (pending.size) {
    const batch = [...pending.entries()].slice(0, MAX_NATIVE_BATCH)

    for (const [key] of batch) {
      pending.delete(key)
    }

    let icons: (string | null)[] = []

    try {
      icons = await extractIcons(batch.map(([, item]) => item.source))
    } catch (error) {
      log.warn('[Dock] native icon extraction failed:', error)
    }

    batch.forEach(([, item], index) => item.resolvers.forEach(resolve => resolve(icons[index] ?? null)))
  }

  reading = false
}

function readNativeIcon(source: IconSource): Promise<string | null> {
  const key = JSON.stringify([source.file, source.index])

  return new Promise(resolve => {
    const existing = pending.get(key)

    if (existing) {
      existing.resolvers.push(resolve)

      return
    }

    pending.set(key, { source, resolvers: [resolve] })

    if (!reading) {
      reading = true
      setImmediate(() => void drainIcons())
    }
  })
}

export async function readWindowsApplicationIcon(target: string, iconPath?: string | null): Promise<string | null> {
  const sources: IconSource[] = []
  let fallback = target

  if (iconPath) {
    sources.push({ file: iconPath, index: 0 })
  }

  if (path.extname(target).toLowerCase() === '.lnk') {
    try {
      const shortcut = shell.readShortcutLink(target)

      if (shortcut.icon) {
        sources.push({ file: shortcut.icon, index: shortcut.iconIndex ?? 0 })
      }

      fallback = shortcut.target
    } catch {
      // 失效快捷方式仍可尝试系统图标。
    }
  }

  sources.push({ file: fallback, index: 0 })
  const seen = new Set<string>()

  for (const source of sources) {
    const key = JSON.stringify([source.file, source.index])

    if (!path.isAbsolute(source.file) || seen.has(key)) {
      continue
    }

    seen.add(key)
    const extension = path.extname(source.file).toLowerCase()

    if (['.png', '.ico'].includes(extension)) {
      const image = nativeImage.createFromPath(source.file)

      if (!image.isEmpty()) {
        return image.resize({ width: ICON_SIZE }).toDataURL()
      }
    } else if (process.platform === 'win32' && ['.exe', '.dll'].includes(extension)) {
      // Electron 的资源解码可能把全 PNG 图标返回为非空的通用 exe 图标。
      const icon = await readNativeIcon(source)

      if (icon) {
        return icon
      }
    }
  }

  if (path.isAbsolute(fallback)) {
    try {
      const image = await app.getFileIcon(fallback, { size: 'large' })

      return image.isEmpty() ? null : image.toDataURL()
    } catch {
      // 图标失败不影响已校验程序的启动与窗口切换。
    }
  }

  return null
}
