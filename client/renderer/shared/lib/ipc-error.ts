import { safeJsonParse } from './safe-json'

const IPC_ENVELOPE_RE = /Error invoking remote method '[^']+': (?:Error|HttpError): ([\s\S]+)$/

/** Error 取 message，其余取 fallback；未给 fallback 时转字符串。 */
export function errorMessage(error: unknown, fallback?: string): string {
  return error instanceof Error ? error.message : (fallback ?? String(error))
}

export function unwrapIpcErrorMessage(error: unknown): string {
  const raw = errorMessage(error)

  return raw.match(IPC_ENVELOPE_RE)?.[1] ?? raw
}

/** 主进程错误文案以 `NNN ` 状态码开头；没有状态码时返回 null。 */
export function ipcErrorStatus(error: unknown): number | null {
  const status = /^(\d{3}) /.exec(unwrapIpcErrorMessage(error))?.[1]

  return status ? Number(status) : null
}

export function isClientErrorIpc(error: unknown): boolean {
  const status = ipcErrorStatus(error)

  return status !== null && status >= 400 && status < 500
}

// 主进程错误形如 `NNN /api/path: {"detail":{"error":"..."}}`：剥掉状态码与路径后取 detail 里的公开文案；解析不了就用调用方兜底。各后端错误展示点共用，避免各自维护解析副本。
export function backendDetailMessage(error: unknown, fallback: string): string {
  const raw = unwrapIpcErrorMessage(error).replace(/^\d{3}\s+(?:\/[^\s]*:\s*)?/, '')
  const detail = safeJsonParse<{ detail?: { error?: unknown } }>(raw, {}).detail?.error

  return typeof detail === 'string' && detail ? detail : fallback
}
