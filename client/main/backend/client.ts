import { resolveTimeoutMs } from '../security/hardening'
import { errorMessage } from '../shared/utils'

interface BackendRequestErrorOptions {
  body?: unknown
  cause?: unknown
  code?: null | string
  message: string
  status?: null | number
}

export class BackendRequestError extends Error {
  body: unknown
  code: null | string
  status: null | number

  constructor({ body, cause, code, message, status }: BackendRequestErrorOptions) {
    super(message)
    this.name = 'BackendRequestError'
    this.status = status ?? null
    this.code = code ?? null
    this.body = body ?? null

    if (cause) {
      this.cause = cause
    }
  }

  get isNetwork(): boolean {
    return this.status === null && this.code !== null
  }

  get isServerError(): boolean {
    return Number.isInteger(this.status) && this.status! >= 500
  }
}

function normalizeBaseUrl(raw?: null | string): string {
  const value = String(raw || '').trim()

  if (!value) {
    throw new BackendRequestError({
      code: 'invalid-base-url',
      message: 'Backend base URL is required.'
    })
  }

  let parsed: URL

  try {
    parsed = new URL(value)
  } catch (error: unknown) {
    const msg = errorMessage(error)
    throw new BackendRequestError({
      code: 'invalid-base-url',
      message: `Backend base URL is not valid: ${msg}`
    })
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new BackendRequestError({
      code: 'invalid-base-url',
      message: `Backend base URL must be http:// or https://, got ${parsed.protocol}`
    })
  }

  parsed.hash = ''
  parsed.search = ''
  parsed.pathname = parsed.pathname.replace(/\/+$/, '')

  return parsed.toString().replace(/\/+$/, '')
}

// JSON → 序列化为 JSON 字符串；string → 原样；Buffer/Uint8Array → 字节。
function encodeBody(body: unknown): { body: string | Buffer | Uint8Array | undefined; contentType: null | string } {
  if (body === undefined || body === null) {
    return { body: undefined, contentType: null }
  }

  if (typeof body === 'string') {
    return { body, contentType: 'text/plain' }
  }

  if (Buffer.isBuffer(body)) {
    return { body, contentType: 'application/octet-stream' }
  }

  if (body instanceof Uint8Array) {
    return { body, contentType: 'application/octet-stream' }
  }

  return { body: JSON.stringify(body), contentType: 'application/json' }
}

interface MinimalFetchResponse {
  headers: { get: (name: string) => null | string }
  ok: boolean
  status: number
  statusText?: string
  text: () => Promise<string>
}

async function decodeResponseBody(res: MinimalFetchResponse): Promise<unknown> {
  const contentType = res.headers.get('content-type') || ''
  const isJson = contentType.includes('application/json')
  const text = await res.text()

  if (!text) {
    return isJson ? null : ''
  }

  if (isJson) {
    try {
      return JSON.parse(text)
    } catch {
      return text
    }
  }

  return text
}

export type FetchFunction = (url: string, init?: RequestInit) => Promise<MinimalFetchResponse | Response>

interface BackendClientOptions {
  baseUrl?: string
  fetch?: FetchFunction
  timeoutMs?: number
  userAgent?: string
}

interface BackendRequestOptions {
  body?: unknown
  headers?: Record<string, string>
  query?: Record<string, unknown>
  signal?: AbortSignal
  timeoutMs?: number
  token?: string
}

export interface BackendClient {
  baseUrl: string
  delete: <T = unknown>(path: string, options?: BackendRequestOptions) => Promise<T>
  get: <T = unknown>(path: string, options?: BackendRequestOptions) => Promise<T>
  patch: <T = unknown>(path: string, options?: BackendRequestOptions) => Promise<T>
  post: <T = unknown>(path: string, options?: BackendRequestOptions) => Promise<T>
  put: <T = unknown>(path: string, options?: BackendRequestOptions) => Promise<T>
  request: <T = unknown>(method: string, path: string, options?: BackendRequestOptions) => Promise<T>
}

export function createBackendClient(options: BackendClientOptions = {}): BackendClient {
  if (typeof options.fetch !== 'function') {
    throw new TypeError('createBackendClient requires a fetch implementation (pass options.fetch)')
  }

  const fetchImpl = options.fetch
  const baseUrl = normalizeBaseUrl(options.baseUrl)
  const defaultTimeoutMs = resolveTimeoutMs(options.timeoutMs)
  const userAgent = options.userAgent || 'SpiritAgentDesktop/0.15 (Electron)'

  async function request<T = unknown>(
    method: string,
    pathStr: string,
    { body, headers, query, signal, timeoutMs, token }: BackendRequestOptions = {}
  ): Promise<T> {
    let url: URL

    try {
      url = new URL(pathStr, baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`)
    } catch (error: unknown) {
      const msg = errorMessage(error)
      throw new BackendRequestError({
        code: 'invalid-path',
        message: `Backend request path is not valid: ${msg}`
      })
    }

    if (query && typeof query === 'object') {
      for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null) {
          continue
        }

        url.searchParams.append(key, String(value))
      }
    }

    const { body: encodedBody, contentType } = encodeBody(body)

    const finalHeaders: Record<string, string> = {
      Accept: 'application/json',
      'User-Agent': userAgent,
      ...(contentType ? { 'Content-Type': contentType } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers || {})
    }

    const effectiveTimeoutMs = resolveTimeoutMs(timeoutMs ?? defaultTimeoutMs)
    const controller = new AbortController()
    const timeoutHandle = setTimeout(() => controller.abort(), effectiveTimeoutMs)

    if (signal) {
      if (signal.aborted) {
        controller.abort()
      } else {
        signal.addEventListener('abort', () => controller.abort(), { once: true })
      }
    }

    let res: MinimalFetchResponse

    try {
      res = await fetchImpl(url.toString(), {
        body: encodedBody,
        headers: finalHeaders,
        method,
        signal: controller.signal
      })
    } catch (error: unknown) {
      clearTimeout(timeoutHandle)
      const errObj = error as { message?: string; name?: string } | undefined
      const isAbort = errObj?.name === 'AbortError'
      const errMessage = errObj?.message || String(error)
      throw new BackendRequestError({
        cause: error,
        code: isAbort ? 'timeout' : 'network-error',
        message: isAbort
          ? `Backend request timed out after ${effectiveTimeoutMs}ms: ${method} ${url.pathname}`
          : `Backend request failed: ${method} ${url.pathname} (${errMessage})`
      })
    }

    clearTimeout(timeoutHandle)

    const payload = await decodeResponseBody(res)

    if (!res.ok) {
      const payloadObj = payload as { detail?: string } | null | undefined

      const detail =
        payloadObj && typeof payloadObj === 'object' && typeof payloadObj.detail === 'string'
          ? payloadObj.detail
          : typeof payload === 'string' && payload
            ? payload
            : `${res.status} ${res.statusText || ''}`.trim()

      throw new BackendRequestError({
        body: payload,
        code: `http-${res.status}`,
        message: `Backend ${method} ${url.pathname} failed: ${detail}`,
        status: res.status
      })
    }

    return payload as T
  }

  return {
    baseUrl,
    delete: (p, opt) => request('DELETE', p, opt),
    get: (p, opt) => request('GET', p, opt),
    patch: (p, opt) => request('PATCH', p, opt),
    post: (p, opt) => request('POST', p, opt),
    put: (p, opt) => request('PUT', p, opt),
    request
  }
}
