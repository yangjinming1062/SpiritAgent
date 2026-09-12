// 渲染层 api() 代理的 path/method 白名单：只放行自家后端产品 API 面。
// 不在名单内的路径（含绝对 URL、协议相对、路径穿越）一律拒绝，防止凭据被拿去打任意 endpoint。
const ALLOWED_API_PREFIXES = ['/api/channels', '/api/companion', '/api/config', '/api/sessions'] as const

const ALLOWED_METHODS = new Set(['DELETE', 'GET', 'PATCH', 'POST', 'PUT'])

function normalizeApiPathname(rawPath: string): string {
  if (!rawPath || rawPath.includes('\0')) {
    throw new Error('API path is required.')
  }

  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(rawPath) || rawPath.startsWith('//')) {
    throw new Error('API path must be a relative path under /api/.')
  }

  let pathname: string

  try {
    pathname = new URL(rawPath, 'http://127.0.0.1').pathname
  } catch {
    throw new Error('API path is invalid.')
  }

  return pathname
}

export function assertApiRequestAllowed(path: unknown, method?: unknown): void {
  const upper = String(method || 'GET').toUpperCase()

  if (!ALLOWED_METHODS.has(upper)) {
    throw new Error(`API method not allowed: ${upper}`)
  }

  const pathname = normalizeApiPathname(String(path || ''))

  const allowed = ALLOWED_API_PREFIXES.some(prefix => pathname === prefix || pathname.startsWith(`${prefix}/`))

  if (!allowed) {
    throw new Error(`API path not allowed: ${pathname}`)
  }
}
