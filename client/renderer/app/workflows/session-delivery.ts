import { $companionSessionId } from '@/modules/conversation'
import { $surfaceOpen, $surfaceRole, requestOpenSurface } from '@/shared/store/surfaces'

// 桌面陪伴提醒进入轻语，完整入口内的陪伴提醒进入生活空间；工作会话由工作台消费。

type WhisperOpener = (sessionId?: string) => void

let openWhisper: WhisperOpener | null = null

export function bindWhisperOpener(next: WhisperOpener): void {
  openWhisper = next
}

export function openSessionSurface(sessionId: string): void {
  if (sessionId === $companionSessionId.get()) {
    if ($surfaceRole.get() !== 'sprite' || $surfaceOpen.get() !== null) {
      void requestOpenSurface('living', { view: 'chat' })

      return
    }

    if (!openWhisper) {
      throw new Error('whisper opener not bound — app bootstrap must initialize first')
    }

    openWhisper(sessionId)

    return
  }

  void requestOpenSurface('workbench', { sessionId, view: 'chat' })
}
