import { $companionSessionId, openWhisper } from '@/features/conversation'
import { $surfaceOpen, $surfaceRole, requestOpenSurface } from '@/shared/store/surfaces'

// 精灵旁的陪伴提醒进入轻语，其余提醒定位唯一主对话。
export function openSessionSurface(sessionId: string): void {
  if (sessionId === $companionSessionId.get()) {
    if ($surfaceRole.get() !== 'sprite' || $surfaceOpen.get()) {
      void requestOpenSurface({ location: { page: 'chat' }, focus: { page: 'chat', sessionId } })

      return
    }

    openWhisper(sessionId)

    return
  }

  void requestOpenSurface({ location: { page: 'chat' }, focus: { page: 'chat', sessionId } })
}
