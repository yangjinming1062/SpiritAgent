import { $chatSessionId, $companionSessionId, switchSession } from '@/modules/conversation'
import { $whisperOpen } from '@/shared/store/chat-visibility'
import { $surfaceOpen, $surfaceRole, requestOpenSurface } from '@/shared/store/surfaces'

export function openWhisper(sessionId?: string): void {
  if (sessionId && sessionId !== $chatSessionId.get()) {
    void switchSession(sessionId)
  }

  $whisperOpen.set(true)
}

// 精灵旁的陪伴提醒进入轻语，其余提醒定位唯一主对话。
export function openSessionSurface(sessionId: string): void {
  if (sessionId === $companionSessionId.get()) {
    if ($surfaceRole.get() !== 'sprite' || $surfaceOpen.get()) {
      void requestOpenSurface({ page: 'chat', sessionId })

      return
    }

    openWhisper(sessionId)

    return
  }

  void requestOpenSurface({ sessionId, page: 'chat' })
}
