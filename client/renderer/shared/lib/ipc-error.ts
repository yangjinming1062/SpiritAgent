import { errorMessage } from '@runtime'

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

/**
 * 失败文案：后端业务正文优先，其次错误自身消息，最后用调用方给的兜底。
 * 业务页面共用此处的错误转换。
 */
export function failureText(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.business.message) {
    return error.business.message
  }

  return errorMessage(error, fallback)
}
