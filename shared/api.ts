import { API_OPERATIONS, type ApiOperations } from './api.generated'
import { ApiError, readHttpError } from './errors'

export type ApiOperationKey = keyof ApiOperations
export type ApiOperation<K extends ApiOperationKey> = ApiOperations[K]
export type ApiInput<K extends ApiOperationKey> = ApiOperation<K>['input']
export type ApiResponse<K extends ApiOperationKey> = ApiOperation<K>['response']
export type ApiPolicy = (typeof API_OPERATIONS)[ApiOperationKey]
export type HttpEncoding = 'json' | 'multipart' | 'binary'
export type HttpResponseKind = 'json' | 'binary' | 'empty'
export type HttpFetch = (url: string, init?: RequestInit) => Promise<Response>

export interface HttpCallOptions {
  timeoutMs?: number
  signal?: AbortSignal
  headers?: RequestInit['headers']
  onResponse?: (response: Response) => void | Promise<void>
}

export type ApiArguments<K extends ApiOperationKey> =
  {} extends ApiInput<K>
    ? [input?: ApiInput<K>, options?: HttpCallOptions]
    : [input: ApiInput<K>, options?: HttpCallOptions]

export interface ApiTransport {
  invoke<K extends ApiOperationKey>(operation: K, ...args: ApiArguments<K>): Promise<ApiResponse<K>>
}

export interface HttpRequestOptions extends HttpCallOptions {
  fetch: HttpFetch
  url: string
  method?: string
  body?: unknown
  encoding?: HttpEncoding
  response?: HttpResponseKind
  credentials?: RequestInit['credentials']
}

export interface HttpClientOptions {
  fetch: HttpFetch
  baseUrl?: string
  defaultTimeoutMs?: number
  credentials?: RequestInit['credentials']
  headers?: () => NonNullable<RequestInit['headers']> | Promise<NonNullable<RequestInit['headers']>>
  onResponse?: (response: Response) => void | Promise<void>
}

export interface ResolvedApiRequest {
  policy: ApiPolicy
  path: string
  method: string
  body: unknown
}

export function getApiOperation(operation: ApiOperationKey): ApiPolicy {
  if (!Object.hasOwn(API_OPERATIONS, operation)) {
    throw new Error(`Unknown API operation: ${operation}`)
  }
  const policy = API_OPERATIONS[operation]
  return policy
}

export function resolveApiRequest<K extends ApiOperationKey>(operation: K, input: ApiInput<K>): ResolvedApiRequest {
  const policy = getApiOperation(operation)
  const parameters: Record<string, unknown> = input.path ?? {}
  let path = policy.path.replace(/\{([^}:]+)(?::[^}]+)?\}/g, (_placeholder, name: string) => {
    const value = parameters[name]
    if (!['string', 'number', 'boolean'].includes(typeof value)) {
      throw new TypeError('Missing or invalid API path parameter')
    }
    const text = String(value)
    if (text.split(/[\\/]/).some(segment => segment === '.' || segment === '..')) {
      throw new TypeError('API path parameters cannot contain dot segments')
    }
    return encodeURIComponent(text)
  })
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(input.query ?? {})) {
    if (Array.isArray(value)) {
      value.forEach(item => query.append(key, String(item)))
    } else if (value !== undefined && value !== null) {
      query.set(key, String(value))
    }
  }
  if (query.size) {
    path += `?${query}`
  }
  return { policy, path, method: policy.method, body: input.body }
}

function requestBody(body: unknown, encoding: HttpEncoding, headers: Headers): RequestInit['body'] {
  if (body === undefined) {
    return undefined
  }
  if (encoding === 'multipart') {
    if (!(body instanceof FormData)) {
      throw new TypeError('Multipart operation requires FormData')
    }
    return body
  }
  if (encoding === 'binary') {
    if (!(body instanceof Uint8Array)) {
      throw new TypeError('Binary operation requires Uint8Array')
    }
    headers.set('Content-Type', 'application/octet-stream')
    return new Uint8Array(body).buffer
  }
  headers.set('Content-Type', 'application/json')
  return JSON.stringify(body)
}

export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  const text = await response.text()
  let payload: unknown
  try {
    payload = JSON.parse(text)
  } catch {
    payload = null
  }
  return new ApiError(
    response.status,
    readHttpError(payload) ?? {
      code: `http_${response.status}`,
      message: `请求失败（${response.status}）`,
      details: null,
      request_id: response.headers.get('X-Request-ID') ?? ''
    }
  )
}

export async function executeHttp(options: HttpRequestOptions): Promise<unknown> {
  const headers = new Headers(options.headers)
  const body = requestBody(options.body, options.encoding ?? 'json', headers)
  const timeoutMs = options.timeoutMs ?? 15_000
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) {
    throw new TypeError('Invalid HTTP timeout')
  }
  const timeout = timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : undefined
  const signal = options.signal && timeout ? AbortSignal.any([options.signal, timeout]) : (options.signal ?? timeout)
  let response: Response
  let processingResponse = false
  try {
    response = await options.fetch(options.url, {
      method: options.method ?? 'GET',
      body,
      headers,
      credentials: options.credentials,
      signal
    })
    if (!response.ok) {
      throw await apiErrorFromResponse(response)
    }
    processingResponse = true
    await options.onResponse?.(response)
    processingResponse = false
    if (options.response === 'empty' || response.status === 204 || options.method === 'HEAD') {
      return undefined
    }
    if (options.response === 'binary') {
      return new Uint8Array(await response.arrayBuffer())
    }
    const text = await response.text()
    try {
      return JSON.parse(text)
    } catch {
      throw new ApiError(response.status, {
        code: 'invalid_response',
        message: '服务器返回了无法读取的数据',
        details: null,
        request_id: response.headers.get('X-Request-ID') ?? ''
      })
    }
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }
    if (processingResponse && !signal?.aborted) {
      throw error
    }
    const code = options.signal?.aborted ? 'cancelled' : timeout?.aborted ? 'timeout' : 'network_error'
    throw new ApiError(
      null,
      {
        code,
        message:
          code === 'cancelled'
            ? '请求已取消'
            : code === 'timeout'
              ? '请求超时，请稍后重试'
              : '无法连接服务器，请检查网络',
        details: null,
        request_id: ''
      },
      { cause: error }
    )
  }
}

export function createHttpClient(options: HttpClientOptions): ApiTransport {
  return {
    async invoke<K extends ApiOperationKey>(operation: K, ...args: ApiArguments<K>): Promise<ApiResponse<K>> {
      const [input, callOptions = {}] = args
      const request = resolveApiRequest(operation, input ?? {})
      const headers = new Headers(await options.headers?.())
      new Headers(callOptions.headers).forEach((value, name) => headers.set(name, value))
      const result = await executeHttp({
        ...callOptions,
        fetch: options.fetch,
        url: `${options.baseUrl?.replace(/\/+$/, '') ?? ''}${request.path}`,
        method: request.method,
        body: request.body,
        encoding: request.policy.encoding,
        response: request.policy.response,
        credentials: options.credentials,
        timeoutMs: callOptions.timeoutMs ?? (request.policy.wait_for_model ? 0 : (options.defaultTimeoutMs ?? 15_000)),
        headers,
        onResponse: async response => {
          await options.onResponse?.(response)
          await callOptions.onResponse?.(response)
        }
      })
      return result as ApiResponse<K>
    }
  }
}
