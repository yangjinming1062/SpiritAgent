import { ApiError } from '../../../../shared/errors'

export function ipcErrorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null
}

export function isClientErrorIpc(error: unknown): boolean {
  const status = ipcErrorStatus(error)

  return status !== null && status >= 400 && status < 500
}

export function backendDetailMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.business.message : fallback
}
