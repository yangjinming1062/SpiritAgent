import { $whisperOpen } from '@/shared/store/chat-visibility'

import { $chatSessionId } from './chat-store'
import { switchSession } from './sessions/selection'

/** 打开轻语；指定会话时先切换，避免把草稿投到旧会话。 */
export function openWhisper(sessionId?: string): void {
  if (sessionId && sessionId !== $chatSessionId.get()) {
    void switchSession(sessionId)
  }

  $whisperOpen.set(true)
}
