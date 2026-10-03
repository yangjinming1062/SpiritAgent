import { useStore } from '@nanostores/react'

import { useConversationView } from './conversation-view'

export function useIsReadOnlySession(): boolean {
  const { $chatSessionReadOnly } = useConversationView().controller

  return useStore($chatSessionReadOnly)
}
