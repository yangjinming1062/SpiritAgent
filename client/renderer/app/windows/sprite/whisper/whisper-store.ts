import { atom } from 'nanostores'

import { $chatSessionId, switchSession } from '@/modules/conversation'
import { $whisperOpen } from '@/shared/store/chat-visibility'

export { $whisperOpen } from '@/shared/store/chat-visibility'

export const $whisperOffset = atom<{ dx: number; dy: number } | null>(null)

export function openWhisper(sessionId?: string): void {
  if (sessionId && sessionId !== $chatSessionId.get()) {
    void switchSession(sessionId)
  }

  $whisperOpen.set(true)
}

export function closeWhisper(): void {
  $whisperOpen.set(false)
}

export function toggleWhisper(): void {
  $whisperOpen.set(!$whisperOpen.get())
}

export function setWhisperOffset(offset: { dx: number; dy: number } | null): void {
  $whisperOffset.set(offset)
}
