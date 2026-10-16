import { API_OPERATIONS, type ApiOperations } from './api.generated'

export type ApiOperationKey = keyof ApiOperations
export type ApiOperation<K extends ApiOperationKey> = ApiOperations[K]
export type ApiInput<K extends ApiOperationKey> = {
  path?: ApiOperation<K>['path']
  query?: ApiOperation<K>['query']
  body?: ApiOperation<K>['body']
}

export interface ApiTransport {
  request<T>(path: string, method?: string, body?: unknown, timeoutMs?: number): Promise<T>
}

export async function requestApi<K extends ApiOperationKey>(
  transport: ApiTransport,
  operation: K,
  input: ApiInput<K> = {}
): Promise<ApiOperation<K>['response']> {
  const policy = API_OPERATIONS.find(value => value.key === operation)
  if (!policy) throw new Error(`Unknown API operation: ${operation}`)
  let path: string = policy.path
  for (const [key, value] of Object.entries(input.path ?? {})) {
    path = path.replace(new RegExp(`\\{${key}(?::[^}]+)?\\}`), encodeURIComponent(String(value)))
  }
  if (/\{[^}]+\}/.test(path)) throw new Error('Missing API path parameter')
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(input.query ?? {})) {
    if (value !== undefined && value !== null) query.set(key, String(value))
  }
  if (query.size) path += `?${query}`
  return transport.request(path, policy.method, input.body, policy.wait_for_model ? 0 : undefined)
}

export function findApiOperation(path: string, method = 'GET'): (typeof API_OPERATIONS)[number] | undefined {
  const pathname = path.split(/[?#]/, 1)[0].replace(/\/$/, '') || '/'
  const candidates = API_OPERATIONS.filter(value => value.method === method.toUpperCase())
    .sort((left, right) => left.path.split('{').length - right.path.split('{').length || right.path.length - left.path.length)
  return candidates.find(value => {
    const routePath = 'route_path' in value && typeof value.route_path === 'string' ? value.route_path : value.path
    const pattern = routePath.replace(/\/$/, '').split(/(\{[^}]+\})/).map(part =>
      part.startsWith('{') ? part.includes(':path') ? '.+' : '[^/]+' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    ).join('')
    return new RegExp(`^${pattern}$`).test(pathname)
  })
}
