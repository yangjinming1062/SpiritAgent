import { $companionSessionId } from '@/modules/conversation'
import { requestOpenSurface } from '@/shared/store/surfaces'

// 陪伴会话交给轻语；工作会话由目标窗口消费 sessionId，来源窗口不切换会话。

type WhisperOpener = (sessionId?: string) => void

let openWhisper: WhisperOpener | null = null

export function bindWhisperOpener(next: WhisperOpener): void {
  openWhisper = next
}

export function openSessionSurface(sessionId: string): void {
  if (sessionId === $companionSessionId.get()) {
    if (!openWhisper) {
      throw new Error('whisper opener not bound — app bootstrap must initialize first')
    }

    openWhisper(sessionId)

    return
  }

  void requestOpenSurface('workbench', { sessionId, view: 'chat' })
}
