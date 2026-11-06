import type { ApiInput, ApiOperationKey, ApiResponse } from '../../../../shared/api'
import type { API_OPERATIONS } from '../../../../shared/api.generated'

import type { DesktopApiResult } from './session'

export interface DesktopAuthSnapshot {
  accountId: string
  baseUrl: null | string
  hasToken: boolean
  sessionId: string
  tokenExpiresAt: null | number
  user: null | { username: string }
}

export interface DesktopActivatePayload {
  code: string
}

export interface DesktopAuthBroadcast {
  authenticated: boolean
  /** 仅明确移除账户时携带；各窗口删除该账户的持久缓存。 */
  removedAccountId?: string
  snapshot: DesktopAuthSnapshot | null
}

export type DesktopApiOperationKey = {
  [Operation in ApiOperationKey]: (typeof API_OPERATIONS)[Operation]['ipc'] extends true ? Operation : never
}[ApiOperationKey]

export interface SpiritAgentApiRequest<Operation extends DesktopApiOperationKey = DesktopApiOperationKey> {
  authSessionId: string
  operation: Operation
  input: ApiInput<Operation>
}

export type DesktopApiInvoke = <Operation extends DesktopApiOperationKey>(
  request: SpiritAgentApiRequest<Operation>
) => Promise<DesktopApiResult<ApiResponse<Operation>>>

export interface SpiritAgentSelectPathsOptions {
  directories?: boolean
  filters?: Array<{ extensions: string[]; name: string }>
  title: string
}
