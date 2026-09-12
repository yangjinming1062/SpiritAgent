import path from 'node:path'

/**
 * 渲染层可读文件白名单：仅允许经对话框 selectPaths 或拖拽/粘贴注册的路径。
 * 防止 XSS / 依赖投毒后 IPC 任意读盘外传（审查问题 10）。
 */
const MAX_ENTRIES = 256
const userSelectedPaths = new Set<string>()

function normalizeKey(filePath: string): string {
  return path.resolve(String(filePath || '').trim())
}

export function registerUserSelectedPaths(paths: readonly string[]): void {
  for (const raw of paths) {
    const key = normalizeKey(raw)

    if (!key) {
      continue
    }

    userSelectedPaths.add(key)

    if (userSelectedPaths.size > MAX_ENTRIES) {
      const oldest = userSelectedPaths.values().next().value

      if (oldest !== undefined) {
        userSelectedPaths.delete(oldest)
      }
    }
  }
}

function isUserSelectedPath(filePath: string): boolean {
  try {
    return userSelectedPaths.has(normalizeKey(filePath))
  } catch {
    return false
  }
}

export function assertUserSelectedPath(filePath: string, purpose: string): void {
  if (!isUserSelectedPath(filePath)) {
    throw new Error(`${purpose} failed: path was not selected by the user in this session.`)
  }
}
