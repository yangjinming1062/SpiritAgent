import { useStore } from '@nanostores/react'

import { useConversationView } from './conversation-view'

export function useIsReadOnlySession(): boolean {
  const { $chatSessionKind } = useConversationView().controller

  return useStore($chatSessionKind) === 'im'
}
