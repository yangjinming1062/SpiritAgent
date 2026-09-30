import fs from 'node:fs'
import path from 'node:path'

import log from 'electron-log/main'

import { atomicWriteFile, safeReadJson } from './utils'

// $SPIRITAGENT_HOME/desktop-config.json 保存用户激活过的后端 URL（与加密会话文件 `agent-session.json` 分离，登出后仍保留）；缺失或格式错乱时返回 null。
const FILENAME = 'desktop-config.json'

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

  const parsed = safeReadJson<Record<string, unknown>>(target)
  const existing = parsed && typeof parsed === 'object' ? parsed : {}
  existing.backendUrl = backendUrl.trim()
  existing.savedAt = Date.now()

  try {
    await atomicWriteFile(target, JSON.stringify(existing, null, 2))

    if (process.platform !== 'win32') {
      // 尽力而为；某些文件系统不支持 chmod
      await fs.promises.chmod(target, 0o600).catch(() => {})
    }

    return true
  } catch (error) {
    log.warn('[config] saving backend URL failed:', error)

    return false
  }
}

// 为会在后面拼接路径后缀（如 /api/update）的调用方做归一化与去尾斜杠；没有配置后端 URL 时返回 null。
export function resolveNormalizedBackendUrl(spiritagentHome: string | null | undefined): string | null {
  const url = readStoredBackendUrl(spiritagentHome)

  return url ? url.replace(/\/+$/, '') : null
}
