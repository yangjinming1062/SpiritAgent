import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import type { DesktopApiOperationKey } from '@ipc/contracts'

import type { ApiInput, ApiOperation } from '../../../../shared/api'

import { type AuthedApiResult, captureAuthScope } from './authed-api'

export type ResourceArguments<Operation extends DesktopApiOperationKey> =
  {} extends ApiInput<Operation> ? [input?: ApiInput<Operation>] : [input: ApiInput<Operation>]

export async function requestResource<K extends DesktopApiOperationKey>(
  operation: K,
  ...args: ResourceArguments<K>
): Promise<ApiOperation<K>['response']> {
  const auth = $auth.get()
  const isCurrent = captureAuthScope()

  if (!isCurrent || auth.kind !== 'authenticated') {
    throw new Error('请先登录')
  }

  const value = await backendApi().api({
    operation,
    input: (args[0] ?? {}) as ApiInput<K>,
    authSessionId: auth.snapshot.sessionId
  })

  if (!isCurrent()) {
    throw new Error('账户已切换，请重新操作')
  }

  return value
}

export async function authedResource<K extends DesktopApiOperationKey>(
  operation: K,
  ...args: ResourceArguments<K>
): Promise<AuthedApiResult<ApiOperation<K>['response']>> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return { ok: false, reason: 'unauth' }
  }

  let result: AuthedApiResult<ApiOperation<K>['response']>

  try {
    const value = await requestResource(operation, ...args)
    result = { ok: true, value }
  } catch (error) {
    result = { ok: false, reason: 'err', error }
  }

  return isCurrent() ? result : { ok: false, reason: 'unauth' }
}
