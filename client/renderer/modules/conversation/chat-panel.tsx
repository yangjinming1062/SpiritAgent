import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useRef, useState } from 'react'

import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'

import type { ConversationVariant } from './chat-dock-message-bubble'
import { ChatParamsPanel, type ChatParamsTab } from './chat-params-panel'
import { $chatSessionId } from './chat-store'
import {
  ChatContextAmbientLine,
  ChatContextCapsule,
  ChatReasoningCapsule,
  ChatTemperatureCapsule
} from './context-progress-bar'
import { ConversationInput } from './conversation-input'
import { ConversationSurface } from './conversation-surface'
import { useChatInput } from './use-chat-input'

export interface ChatPanelProps {
  className?: string
  gatewayState: ConnectionState
  inputWrapperClassName?: string
  isReadOnlySession: boolean
  scrollRef: RefObject<HTMLDivElement | null>
  surfaceClassName?: string
  variant: ConversationVariant
}

export function ChatPanel({
  className,
  gatewayState,
  inputWrapperClassName,
  isReadOnlySession,
  scrollRef,
  surfaceClassName,
  variant
}: ChatPanelProps): React.JSX.Element {
  const chatSessionId = useStore($chatSessionId)
  const [paramsPanelOpen, setParamsPanelOpen] = useState(false)
  const [paramsPanelTab, setParamsPanelTab] = useState<ChatParamsTab>('context')
  const paramsPanelRef = useRef<HTMLDivElement>(null)

  const input = useChatInput({ gatewayState, isReadOnlySession })

  useEffect(() => {
    if (gatewayState === 'open') {
      void fetchSlashCommandMeta()
    }
  }, [gatewayState])

  // 点击面板外部 / ESC 关闭参数面板
  useEffect(() => {
    if (!paramsPanelOpen) {
      return
    }

    const handlePointerDown = (e: PointerEvent) => {
      const target = e.target as Node | null

      if (!target || !paramsPanelRef.current?.contains(target)) {
        setParamsPanelOpen(false)
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setParamsPanelOpen(false)
      }
    }

    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [paramsPanelOpen])

  const headerWrapClass = cn(
    'relative flex items-center justify-end shrink-0',
    variant === 'workbench'
      ? 'border-b border-line-hairline px-4 py-2.5 bg-transparent'
      : 'border-0 px-4 pt-3 pb-1 bg-transparent'
  )

  const openParamsPanel = (tab: ChatParamsTab): void => {
    if (paramsPanelOpen && paramsPanelTab === tab) {
      setParamsPanelOpen(false)

      return
    }

    setParamsPanelTab(tab)
    setParamsPanelOpen(true)
  }

  return (
    <div className={cn('flex flex-col flex-1 h-full min-h-0', className)}>
      <div className={headerWrapClass}>
        <div
          className="flex items-center gap-1.5"
          onPointerDown={e => {
            // 阻止冒泡到 window 上的外点击监听器,避免点触发按钮时立刻收起
            e.stopPropagation()
          }}
        >
          <ChatContextCapsule
            active={paramsPanelOpen && paramsPanelTab === 'context'}
            onClick={() => openParamsPanel('context')}
            variant={variant}
          />
          <ChatTemperatureCapsule
            active={paramsPanelOpen && paramsPanelTab === 'temperature'}
            onClick={() => openParamsPanel('temperature')}
            variant={variant}
          />
          <ChatReasoningCapsule
            active={paramsPanelOpen && paramsPanelTab === 'reasoning'}
            onClick={() => openParamsPanel('reasoning')}
            variant={variant}
          />
        </div>
        {paramsPanelOpen && (
          <div
            className="absolute right-3 top-11 z-50 animate-in fade-in slide-in-from-top-2 duration-150"
            ref={paramsPanelRef}
          >
            <ChatParamsPanel
              activeTab={paramsPanelTab}
              key={chatSessionId}
              onClose={() => setParamsPanelOpen(false)}
              onTabChange={setParamsPanelTab}
              sessionId={chatSessionId}
            />
          </div>
        )}
      </div>
      {variant === 'workbench' && <ChatContextAmbientLine />}
      <ConversationSurface
        className={cn('flex-1 min-h-0 overflow-y-auto', surfaceClassName)}
        scrollRef={scrollRef}
        variant={variant}
      />
      <div className={cn('mt-auto shrink-0', inputWrapperClassName)}>
        <ConversationInput {...input.inputProps} variant={variant} />
      </div>
    </div>
  )
}
