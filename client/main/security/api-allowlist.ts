import type { DesktopApiOperationKey } from '@ipc/contracts'

import type { ApiOperationKey } from '../../../shared/api'
import { API_OPERATIONS } from '../../../shared/api.generated'

export function assertApiOperationAllowed(operation: unknown): asserts operation is DesktopApiOperationKey {
  if (typeof operation !== 'string' || !Object.hasOwn(API_OPERATIONS, operation)) {
    throw new Error('Unknown API operation')
  }

  const policy = API_OPERATIONS[operation as ApiOperationKey]

  if (!policy.identities.some(identity => identity === 'desktop') || !('ipc' in policy && policy.ipc)) {
    throw new Error('API operation is not available to desktop views: ' + operation)
  }
}
