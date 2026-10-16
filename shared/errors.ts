function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export interface BusinessError {
  code: string
  message: string
  details: unknown
  request_id: string
}

function readBusinessError(value: unknown): BusinessError | null {
  if (
    !isRecord(value) ||
    typeof value.code !== 'string' ||
    typeof value.message !== 'string' ||
    typeof value.request_id !== 'string'
  ) {
    return null
  }
  return { code: value.code, message: value.message, details: value.details, request_id: value.request_id }
}

export function readHttpError(value: unknown): BusinessError | null {
  return isRecord(value) ? readBusinessError(value.error) : null
}

export class ApiError extends Error {
  constructor(
    readonly status: number | null,
    readonly business: BusinessError
  ) {
    super(business.message)
    this.name = 'ApiError'
  }
}
