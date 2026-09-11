import { $companionSessionId, switchSession } from '@/modules/conversation'
import { requestOpenSurface } from '@/shared/store/surfaces'

// 会话送达工作流：通知 / 主动消息点击后把用户带到正确的会话表面。
// 陪伴会话进轻语卡片；工作会话切会话并打开工作台聊天视图。
// 直接 openWhisper(工作会话 id) 会被强制拉回主陪伴会话并丢掉目标内容。
// 轻语开启器由 app/bootstrap 绑定——工作流不反向导入窗口组件。

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

  void switchSession(sessionId)
  void requestOpenSurface('workbench', { sessionId, view: 'chat' })
}
