const IPC_ENVELOPE_RE = /Error invoking remote method '[^']+': Error: (.+)$/

export function unwrapIpcErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)

  return raw.match(IPC_ENVELOPE_RE)?.[1] ?? raw
}

export function isClientErrorIpc(error: unknown): boolean {
  return /^4\d\d /.test(unwrapIpcErrorMessage(error))
}
