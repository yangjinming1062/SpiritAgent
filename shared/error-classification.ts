import { ApiError } from './errors'

/**
 * 跨端统一的失败归类。Client（桌面渲染层）与 Remote（手机网页）对同一故障必须给出同一判定，
 * 各自只负责把归类映射成本端的界面文案，因此归类逻辑放在两端共用的契约包里。
 *
 * 优先使用结构化字段（HTTP 状态与业务错误码）；只有在拿不到结构化信息时，才回落到消息文本匹配。
 */
export type ErrorKind =
  /** 断连、超时等传输层失败 */
  | 'network'
  /** 限流或周期配额用尽 */
  | 'rate_limited'
  /** 供应商鉴权失败、未配置或暂时不可用 */
  | 'provider'
  /** 服务端内部错误 */
  | 'server'
  /** 登录失效 */
  | 'unauthorized'
  /** 无法归类的失败 */
  | 'unknown'

const PROVIDER_CODES = new Set(['provider_auth', 'provider_unavailable', 'unavailable'])

const RATE_LIMIT_CODES = new Set([
  'rate_limit',
  'rate_limit_exceeded',
  'resource_exhausted',
  'insufficient_quota',
  'insufficient_credits'
])

function classifyStructured(error: unknown): ErrorKind | null {
  if (!(error instanceof ApiError)) {
    return null
  }

  const code = error.code

  // 主动取消不是失败，交回调用方自己的取消语义。
  if (code === 'cancelled') {
    return 'unknown'
  }

  if (error.isLoginExpired || code === 'unauthenticated') {
    return 'unauthorized'
  }

  if (error.status === 429 || RATE_LIMIT_CODES.has(code)) {
    return 'rate_limited'
  }

  if (PROVIDER_CODES.has(code)) {
    return 'provider'
  }

  if (error.isNetwork) {
    return 'network'
  }

  if (error.status === null) {
    return 'network'
  }

  if (error.status >= 500) {
    return 'server'
  }

  return null
}

function classifyMessage(message: string): ErrorKind {
  if (/failed to fetch|networkerror|load failed|fetch failed|econnrefused|请求超时|无法连接/i.test(message)) {
    return 'network'
  }

  if (/429|quota|rate.?limit|额度|配额|too many requests/i.test(message)) {
    return 'rate_limited'
  }

  if (/provider|supplier|api.?key|invalid_api_key|未配置|供应商|模型卡片|incorrect api key/i.test(message)) {
    return 'provider'
  }

  if (/traceback|internal server|exception|请求失败（5\d\d）|internal_error/i.test(message)) {
    return 'server'
  }

  if (/401|unauthenticated|登录已失效|登录状态/i.test(message)) {
    return 'unauthorized'
  }

  return 'unknown'
}

/** 取失败的可读正文，用于没有结构化错误时的文本回落。 */
export function errorMessageText(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }

  if (typeof error === 'string') {
    return error
  }

  return ''
}

/** 把任意失败归入跨端统一的失败类别。 */
export function classifyError(error: unknown): ErrorKind {
  return classifyStructured(error) ?? classifyMessage(errorMessageText(error))
}
