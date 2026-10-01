import { $chatSessionId, $companionSessionId, switchSession } from '@/modules/conversation'
import { $whisperOpen } from '@/shared/store/chat-visibility'
import { $surfaceOpen, $surfaceRole, requestOpenSurface } from '@/shared/store/surfaces'

export function openWhisper(sessionId?: string): void {
  if (sessionId && sessionId !== $chatSessionId.get()) {
    void switchSession(sessionId)
  }

  $whisperOpen.set(true)
}

// 桌面陪伴提醒进入轻语，完整入口内的陪伴提醒进入生活空间；工作会话由工作台消费。
export function openSessionSurface(sessionId: string): void {
  if (sessionId === $companionSessionId.get()) {
    if ($surfaceRole.get() !== 'sprite' || $surfaceOpen.get() !== null) {
      void requestOpenSurface('living', { view: 'chat' })

      return
    }

    openWhisper(sessionId)

    return
  }

  void requestOpenSurface('workbench', { sessionId, view: 'chat' })
}
