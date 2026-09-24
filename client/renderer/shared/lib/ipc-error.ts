const IPC_ENVELOPE_RE = /Error invoking remote method '[^']+': (?:Error|HttpError): ([\s\S]+)$/

export function unwrapIpcErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)

  return raw.match(IPC_ENVELOPE_RE)?.[1] ?? raw
}

export function isClientErrorIpc(error: unknown): boolean {
  return /^4\d\d /.test(unwrapIpcErrorMessage(error))
}

// 主进程错误形如 `NNN /api/path: {"detail":{"error":"..."}}`：剥掉状态码与路径后取 detail
// 里的公开文案；解析不了就用调用方兜底。各后端错误展示点共用，避免各自维护解析副本。
export function backendDetailMessage(error: unknown, fallback: string): string {
  const raw = unwrapIpcErrorMessage(error).replace(/^\d{3}\s+(?:\/[^\s]*:\s*)?/, '')

  try {
    const parsed = JSON.parse(raw) as { detail?: { error?: unknown } }
    const backendError = parsed?.detail?.error

    if (typeof backendError === 'string' && backendError) {
      return backendError
    }
  } catch {
    /* 非预期形态，走兜底文案 */
  }

  return fallback
}
