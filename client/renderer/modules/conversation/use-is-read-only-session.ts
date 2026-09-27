import { useStore } from '@nanostores/react'

import { $chatSessionKind } from './chat-store'

export function useIsReadOnlySession(): boolean {
  return useStore($chatSessionKind) === 'im'
}
