// 对话表面（消息流）：两个入口（生活空间、工作台）共用的中间栏组件。
//
// 仅负责把消息列表、typing 占位、流式滚动跟随三件事渲染出来；
// 头部拖拽、参数面板、会话侧栏、输入胶囊都归各自的宿主容器管理——
// 生活空间左栏 + 右栏布局、工作台三栏布局形态差异大，共用部分只此一处。

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
import {
  $chatMessageList,
  $chatSessionId,
  $chatStreamingTick,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingPromptBatch
} from './chat-store'
import { collectTimeDividerIds } from './conversation-time'
import { consumePendingMessages, pendingMessages } from './pending-messages'

interface ConversationSurfaceProps {
  className?: string
  emptyHint?: string
  scrollRef: RefObject<HTMLDivElement | null>
  variant?: ConversationVariant
}

export function ConversationSurface({
  className,
  emptyHint,
  scrollRef,
  variant = 'living'
}: ConversationSurfaceProps): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const pending = useStore(pendingMessages.$atom)
  const surfaceOpen = useStore($surfaceOpen)
  const surfaceRole = useStore($surfaceRole)
  useEffect(() => {
    if (
      sessionId &&
      surfaceOpen !== null &&
      surfaceOpen === surfaceRole &&
      pending.some(item => item.sessionId === sessionId)
    ) {
      consumePendingMessages(sessionId)
    }
  }, [sessionId, pending, surfaceOpen, surfaceRole])
  const dict = useStrings()
  const list = useStore($chatMessageList)
  const lastAssistantStreaming = useStore($lastAssistantStreaming)
  const chatTurnInFlight = useStore($chatTurnInFlight)
  const pendingPromptBatch = useStore($pendingPromptBatch)
  const gatewayState = useStore($gatewayState)

  const resolvedEmptyHint = emptyHint ?? dict.chat.emptyHint

  const isTurnPendingOrInFlight = pendingPromptBatch.length > 0 || chatTurnInFlight
  const showTyping = isTurnPendingOrInFlight && !lastAssistantStreaming && gatewayState === 'open'

  // 流式 tick 仅驱动跟滚，不进入渲染路径——listen 回调直接动 DOM，
  // 避免每个 token 把整个消息流（list.map + 多个 MessageBubble）重新走一遍。
  useEffect(() => {
    const el = scrollRef.current

    el?.scrollTo?.({ top: el.scrollHeight, behavior: 'smooth' })
  }, [scrollRef])

  useAtomListen($chatStreamingTick, () => {
    const el = scrollRef.current

    el?.scrollTo?.({ top: el.scrollHeight, behavior: 'smooth' })
  })

  // 列表长度变化同样需要跟滚。
  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [list.length, scrollRef])

  const portraitUrl = useStore(presentationPorts().$portraitUrl)
  const activeAvatarId = useStore(presentationPorts().$activeAvatarId)
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
            {resolvedEmptyHint}
          </span>
        </div>
      )}
      {list.map(item => (
        <MessageBubble key={item.id} message={item} showTimeLabel={showTimeSet.has(item.id)} variant={variant} />
      ))}
      {showTyping && (
        <div className="flex shrink-0 items-start gap-2.5">
          {variant === 'workbench' && (
            <div className="mt-0.5 size-8 shrink-0 overflow-hidden rounded-full border border-white/15 bg-white/10 shadow-sm">
              {portraitUrl ? (
                <img alt="Companion" className="size-full object-cover" src={portraitUrl} />
              ) : activeAvatarId == null ? (
                <div className="flex size-full items-center justify-center bg-gradient-to-tr from-blue-600 to-indigo-500 text-[11px] font-bold text-white">
                  S
                </div>
              ) : null}
            </div>
          )}
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
