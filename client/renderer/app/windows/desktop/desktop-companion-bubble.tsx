import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef } from 'react'

import { $effectiveTier } from '@/modules/character'
import { $chatSessionId, $companionSessionId, pendingMessages } from '@/modules/conversation'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { $desktopChatActive } from '@/shared/store/chat-visibility'

import styles from './desktop.module.css'

// 桌面模式没有精灵旁气泡：底栏头像承接待读的主动消息，进入陪伴会话才清除。
export function DesktopCompanionBubble({
  visible,
  onOpen
}: {
  visible: boolean
  onOpen: () => void
}): React.JSX.Element | null {
  const pending = useStore(pendingMessages.$atom)
  const companionSessionId = useStore($companionSessionId)
  const chatSessionId = useStore($chatSessionId)
  const chatActive = useStore($desktopChatActive)
  const tier = useStore($effectiveTier)

  // 陪伴会话正在被阅读时不再叠加提醒，与待读清除的口径一致。
  const readingCompanion = chatActive && companionSessionId !== null && chatSessionId === companionSessionId

  const text =
    visible && tier !== 'still' && !readingCompanion
      ? (pending.findLast(item => item.sessionId === companionSessionId)?.text ?? null)
      : null

  return text ? <CompanionBubbleView onOpen={onOpen} text={text} /> : null
}

function CompanionBubbleView({ text, onOpen }: { text: string; onOpen: () => void }): React.JSX.Element {
  const bubbleRef = useRef<HTMLButtonElement>(null)
  // 桌面窗只命中交互区域：气泡单独登记，否则第一次点击会落到下方桌面。
  useInteractiveRegion('desktop-companion-bubble', bubbleRef)

  return (
    <button className={styles.companionBubble} onClick={onOpen} ref={bubbleRef} title={text} type="button">
      <span className={styles.companionBubbleText}>{text}</span>
    </button>
  )
}
