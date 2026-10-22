export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

/** 校验值可安全 JSON 序列化；ancestors 记录在途对象，循环引用按非法处理而不是递归溢出。 */
export function isJsonValue(value: unknown, ancestors = new Set<object>()): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') {
    return true
  }
  if (typeof value === 'number') {
    return Number.isFinite(value)
  }
  if (typeof value !== 'object' || ancestors.has(value)) {
    return false
  }

  ancestors.add(value)

  let valid: boolean
  if (Array.isArray(value)) {
    valid = value.every(item => isJsonValue(item, ancestors))
  } else if (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null) {
    valid = Object.values(value).every(item => isJsonValue(item, ancestors))
  } else {
    valid = false
  }

  ancestors.delete(value)

  return valid
}

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
    readonly business: BusinessError,
    options?: ErrorOptions
  ) {
    super(business.message, options)
    this.name = 'ApiError'
  }

  get code(): string {
    return this.business.code
  }
  get isNetwork(): boolean {
    return this.status === null && ['network_error', 'timeout'].includes(this.code)
  }
  get isServerError(): boolean {
    return this.status !== null && this.status >= 500
  }
  get isLoginExpired(): boolean {
    return this.status === 401 && this.code === 'unauthenticated'
  }
}
