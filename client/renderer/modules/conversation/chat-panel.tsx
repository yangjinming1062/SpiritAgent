import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useId, useRef, useState } from 'react'

import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { SlidersHorizontal } from '@/shared/lib/icons'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import type { ConversationVariant } from './chat-dock-message-bubble'
import { ChatParamsPanel, type ChatParamsTab } from './chat-params-panel'
import { ChatContextAmbientLine } from './context-progress-bar'
import { ConversationInput } from './conversation-input'
import { ConversationSurface } from './conversation-surface'
import { useConversationView } from './conversation-view'
import { useChatInput } from './use-chat-input'

export interface ChatPanelProps {
  className?: string
  heading?: React.ReactNode
  notice?: React.ReactNode
  groupAvatars?: boolean
  reveal?: { key: string; request: number }
  gatewayState: ConnectionState
  inputWrapperClassName?: string
  isReadOnlySession: boolean
  scrollRef: RefObject<HTMLDivElement | null>
  surfaceClassName?: string
  variant: ConversationVariant
}

export function ChatPanel({
  className,
  heading,
  notice,
  groupAvatars,
  reveal,
  gatewayState,
  inputWrapperClassName,
  isReadOnlySession,
  scrollRef,
  surfaceClassName,
  variant
}: ChatPanelProps): React.JSX.Element {
  const { controller, eligible } = useConversationView()
  const { $chatSessionId } = controller

  const chatSessionId = useStore($chatSessionId)
  const [paramsPanelOpen, setParamsPanelOpen] = useState(false)
  const [paramsPanelTab, setParamsPanelTab] = useState<ChatParamsTab>('context')
  const paramsPanelRef = useRef<HTMLDivElement>(null)
  const paramsButtonRef = useRef<HTMLButtonElement>(null)
  const paramsPanelId = useId()

  const input = useChatInput({ gatewayState, isReadOnlySession })
  const dict = useStrings()

  useEffect(() => {
    if (gatewayState === 'open') {
      void fetchSlashCommandMeta()
    }
  }, [gatewayState])

  const closeParamsPanel = (): void => {
    setParamsPanelOpen(false)
    paramsButtonRef.current?.focus()
  }

  useEscapeKey(closeParamsPanel, { capture: false, enabled: paramsPanelOpen && eligible })

  useEffect(() => {
    if (!paramsPanelOpen || !eligible) {
      return
    }

    paramsPanelRef.current?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus()

    const onPointerDown = (event: PointerEvent): void => {
      if (
        !paramsPanelRef.current?.contains(event.target as Node | null) &&
        !paramsButtonRef.current?.contains(event.target as Node | null)
      ) {
        setParamsPanelOpen(false)
      }
    }

    window.addEventListener('pointerdown', onPointerDown)

    return () => window.removeEventListener('pointerdown', onPointerDown)
  }, [paramsPanelOpen, eligible])

  useEffect(() => {
    if (!eligible) {
      setParamsPanelOpen(false)
    }
  }, [eligible])

  useEffect(() => setParamsPanelOpen(false), [chatSessionId])

  const headerWrapClass = cn(
    'flex items-center justify-end shrink-0',
    variant === 'assistant'
      ? 'border-b border-line-hairline px-4 py-2.5 bg-transparent'
      : 'border-0 px-4 pt-3 pb-1 bg-transparent'
  )

  const toggleParamsPanel = (): void => {
    if (!eligible) {
      return
    }

    setParamsPanelOpen(!paramsPanelOpen)
  }

  return (
    <div className={cn('relative flex flex-col flex-1 h-full min-h-0', className)}>
      <div className={headerWrapClass}>
        {heading}
        <button
          aria-controls={paramsPanelId}
          aria-expanded={paramsPanelOpen}
          aria-label={dict.chat.params.openPanel}
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-lg border border-transparent text-muted transition hover:border-line-standard hover:bg-fill-hover hover:text-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40',
            paramsPanelOpen && 'border-accent-line bg-accent-soft text-accent'
          )}
          disabled={!eligible || chatSessionId === null}
          onClick={toggleParamsPanel}
          ref={paramsButtonRef}
          title={dict.chat.params.openPanel}
          type="button"
        >
          <SlidersHorizontal size={18} />
        </button>
      </div>
      {paramsPanelOpen && (
        <div
          aria-label={dict.chat.params.openPanel}
          className="absolute right-3 top-14 z-50 max-h-[calc(100%_-_70px)] w-[min(350px,calc(100%_-_24px))] overflow-y-auto rounded-2xl shadow-2xl animate-in fade-in slide-in-from-top-2 duration-150"
          id={paramsPanelId}
          ref={paramsPanelRef}
          role="dialog"
        >
          <ChatContextAmbientLine />
          <ChatParamsPanel
            activeTab={paramsPanelTab}
            key={chatSessionId}
            onClose={closeParamsPanel}
            onTabChange={setParamsPanelTab}
            sessionId={chatSessionId}
          />
        </div>
      )}
      {notice}
      <ConversationSurface
        className={cn('flex-1 min-h-0 overflow-y-auto', surfaceClassName)}
        groupAvatars={groupAvatars}
        reveal={reveal}
        scrollRef={scrollRef}
        variant={variant}
      />
      <div className={cn('mt-auto shrink-0', inputWrapperClassName)}>
        {isReadOnlySession ? (
          <p className="py-1 text-center text-xs text-muted" role="status">
            {dict.chat.input.readOnlyHint}
          </p>
        ) : (
          <ConversationInput {...input.inputProps} variant={variant} />
        )}
      </div>
    </div>
  )
}
