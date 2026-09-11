import { onMount } from 'nanostores'

import { definePersistedAtom } from '@/shared/lib/storage'

interface PendingMessage {
  sessionId: string
  text: string
}

export const pendingMessages = definePersistedAtom<PendingMessage[]>({
  key: 'da.companion.pendingMessages',
  fallback: []
})

export function rememberPendingMessage(sessionId: string, text: string): void {
  pendingMessages.set([...pendingMessages.get().filter(item => item.sessionId !== sessionId), { sessionId, text }])
}

export function consumePendingMessages(sessionId: string): void {
  pendingMessages.set(pendingMessages.get().filter(item => item.sessionId !== sessionId))
}

onMount(pendingMessages.$atom, () => {
  const refresh = (event: StorageEvent): void => {
    if (event.key !== 'da.companion.pendingMessages') {
      return
    }

    try {
      const value: unknown = JSON.parse(event.newValue ?? '[]')

      if (
        Array.isArray(value) &&
        value.every(item => typeof item?.sessionId === 'string' && typeof item?.text === 'string')
      ) {
        pendingMessages.$atom.set(value)
      }
    } catch {
      /* Ignore malformed local storage. */
    }
  }

  window.addEventListener('storage', refresh)

  return () => window.removeEventListener('storage', refresh)
})
