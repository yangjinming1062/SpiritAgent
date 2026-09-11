import fs from 'node:fs'
import path from 'node:path'

import { atomicWriteFile, safeReadJson } from './utils'

// $SPIRITAGENT_HOME/desktop-config.json 保存用户激活过的后端 URL
//（与加密会话文件 `agent-session.json` 分离，便于在登出后仍然保留）。
// 尽力处理：文件缺失或格式错乱时返回 null。
export const FILENAME = 'desktop-config.json'

function configPath(spiritagentHome: string | null | undefined): string | null {
  if (!spiritagentHome) {
    return null
  }

  return path.join(spiritagentHome, FILENAME)
}

export function readStoredBackendUrl(spiritagentHome: string | null | undefined): string | null {
  const target = configPath(spiritagentHome)

  if (!target) {
    return null
  }

  const parsed = safeReadJson<{ backendUrl?: unknown }>(target)

  if (parsed && typeof parsed.backendUrl === 'string' && parsed.backendUrl.trim()) {
    return parsed.backendUrl.trim()
  }

  return null
}

export async function writeStoredBackendUrl(
  spiritagentHome: string | null | undefined,
  backendUrl: string
): Promise<boolean> {
  const target = configPath(spiritagentHome)

  if (!target || typeof backendUrl !== 'string' || !backendUrl.trim()) {
    return false
  }

  let existing: Record<string, unknown> = {}

  try {
    const raw = await fs.promises.readFile(target, 'utf8')
    const parsed = JSON.parse(raw)

    if (parsed && typeof parsed === 'object') {
      existing = parsed as Record<string, unknown>
    }
  } catch {
    existing = {}
  }

  existing.backendUrl = backendUrl.trim()
  existing.savedAt = Date.now()

  try {
    await atomicWriteFile(target, JSON.stringify(existing, null, 2))

    if (process.platform !== 'win32') {
      try {
        await fs.promises.chmod(target, 0o600)
      } catch {
        // 尽力而为；某些文件系统不支持 chmod
      }
    }

    return true
  } catch {
    return false
  }
}

// 为会在后面拼接路径后缀（如 /api/update）的调用方做归一化与去尾斜杠。
// 没有配置后端 URL 时返回 null。
export function resolveNormalizedBackendUrl(spiritagentHome: string | null | undefined): string | null {
  const url = readStoredBackendUrl(spiritagentHome)

  return url ? String(url).replace(/\/+$/, '') : null
}
