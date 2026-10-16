import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'

import { type ApiInput, type ApiOperation, type ApiOperationKey, requestApi } from '../../../../shared/api'

import { type AuthedApiResult, captureAuthScope } from './authed-api'

export async function requestResource<K extends ApiOperationKey>(
  operation: K,
  input: ApiInput<K> = {}
): Promise<ApiOperation<K>['response']> {
  const auth = $auth.get()
  const isCurrent = captureAuthScope()

  if (!isCurrent || auth.kind !== 'authenticated') {
    throw new Error('请先登录')
  }

  const value = await requestApi(
    {
      request: (path, method, body) =>
        backendApi().api({
          path,
          method,
          body,
          authSessionId: auth.snapshot.sessionId
        })
    },
    operation,
    input
  )

  if (!isCurrent()) {
    throw new Error('账户已切换，请重新操作')
  }

  return value
}

export async function authedResource<K extends ApiOperationKey>(
  operation: K,
  input: ApiInput<K> = {}
): Promise<AuthedApiResult<ApiOperation<K>['response']>> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return { ok: false, reason: 'unauth' }
  }

  let result: AuthedApiResult<ApiOperation<K>['response']>

  try {
    const value = await requestResource(operation, input)
    result = { ok: true, value }
  } catch (error) {
    result = { ok: false, reason: 'err', error }
  }

  return isCurrent() ? result : { ok: false, reason: 'unauth' }
}
