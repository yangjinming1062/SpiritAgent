import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useMemo } from 'react'

import { useAtomListen } from '@/shared/hooks/use-atom-listen'
import { cn } from '@/shared/lib/utils'
import { presentationPorts } from '@/shared/presentation-ports'
import { $gatewayState } from '@/shared/store/gateway'
import { $surfaceOpen, $surfaceRole } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { type ConversationVariant, MessageBubble } from './chat-dock-message-bubble'
import { CompanionAvatar } from './companion-avatar'
import { collectTimeDividerIds } from './conversation-time'
import { useConversationView } from './conversation-view'
import { consumePendingMessages, pendingMessages } from './pending-messages'
import { conversationVoiceSink } from './voice-link'

interface ConversationSurfaceProps {
  className?: string
  scrollRef: RefObject<HTMLDivElement | null>
  variant?: ConversationVariant
}

function scrollToBottom(el: HTMLElement | null): void {
  el?.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}

export function ConversationSurface({
  className,
  scrollRef,
  variant = 'living'
}: ConversationSurfaceProps): React.JSX.Element {
  const view = useConversationView()

  const {
    $chatMessageList,
    $chatSessionId,
    $chatStreamingTick,
    $chatTurnInFlight,
    $lastAssistantStreaming,
    $pendingPromptBatch
  } = view.controller

  const locked = useStore(presentationPorts().$screenLocked)
  const sessionId = useStore($chatSessionId)
  useEffect(() => {
    if (view.scoped || variant !== 'living') {
      return
    }

    conversationVoiceSink().setVisible(true)

    return () => conversationVoiceSink().setVisible(false)
  }, [sessionId, variant, view.scoped])
  const pending = useStore(pendingMessages.$atom)
  const surfaceOpen = useStore($surfaceOpen)
  const surfaceRole = useStore($surfaceRole)
  useEffect(() => {
    if (
      sessionId &&
      !locked &&
      view.eligible &&
      (view.scoped || (surfaceOpen !== null && surfaceOpen === surfaceRole)) &&
      pending.some(item => item.sessionId === sessionId)
    ) {
      consumePendingMessages(sessionId)
    }
  }, [sessionId, pending, surfaceOpen, surfaceRole, locked, view.eligible, view.scoped])
  const dict = useStrings()
  const list = useStore($chatMessageList)
  const lastAssistantStreaming = useStore($lastAssistantStreaming)
  const chatTurnInFlight = useStore($chatTurnInFlight)
  const pendingPromptBatch = useStore($pendingPromptBatch)
  const gatewayState = useStore($gatewayState)

  const isTurnPendingOrInFlight = pendingPromptBatch.length > 0 || chatTurnInFlight
  const showTyping = isTurnPendingOrInFlight && !lastAssistantStreaming && gatewayState === 'open'

  // 流式 tick 仅驱动跟滚，不进入渲染路径—— listen 回调直接动 DOM，避免每个 token 把整个消息流（list.map + 多个 MessageBubble）重新走一遍。
  useAtomListen($chatStreamingTick, () => {
    scrollToBottom(scrollRef.current)
  })

  // 挂载与列表长度变化同样需要跟滚。
  useEffect(() => {
    scrollToBottom(scrollRef.current)
  }, [list.length, scrollRef])

  const showTimeSet = useMemo(() => collectTimeDividerIds(list), [list])

  return (
    <div
      className={cn('space-y-3', className ?? 'flex-1 overflow-y-auto px-4 py-4')}
      data-surface={variant}
      ref={scrollRef}
    >
      {list.length === 0 && (
        <div className="flex flex-1 items-center justify-center py-10 pointer-events-none select-none">
          <span
            className={cn(
              'text-center text-sm',
              variant === 'living'
                ? 'inline-flex items-center gap-2 rounded-full border border-line-hairline bg-surface-card/75 px-4 py-1.5 text-xs text-strong/90 shadow-sm backdrop-blur-xl backdrop-saturate-180'
                : 'text-faint'
            )}
          >
            {dict.chat.emptyHint}
          </span>
        </div>
      )}
      {list.map(item => (
        <MessageBubble key={item.id} message={item} showTimeLabel={showTimeSet.has(item.id)} variant={variant} />
      ))}
      {showTyping && (
        <div className="flex shrink-0 items-start gap-2.5">
          {variant === 'workbench' && <CompanionAvatar />}
          <div
            className={
              variant === 'workbench'
                ? 'flex items-center gap-1.5 rounded-2xl border border-line-standard bg-surface-card px-4 py-3 shadow-xs backdrop-blur-md'
                : 'rounded-2xl border border-line-hairline bg-surface-card px-3.5 py-2.5 text-faint'
            }
          >
            {variant === 'workbench' ? (
              <>
                <span className="size-1.5 animate-bounce rounded-full bg-blue-400 [animation-delay:-0.3s]" />
                <span className="size-1.5 animate-bounce rounded-full bg-blue-400 [animation-delay:-0.15s]" />
                <span className="size-1.5 animate-bounce rounded-full bg-blue-400" />
              </>
            ) : (
              dict.chat.typing
            )}
          </div>
        </div>
      )}
    </div>
  )
}
