import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useMemo, useRef, useState } from 'react'

import { attachVideoFile } from '@/chat/chat-attach-picker'
import type { ConversationVariant } from '@/chat/chat-dock-message-bubble'
import { ChatParamsPanel, type ChatParamsTab } from '@/chat/chat-params-panel'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  $pendingPromptBatch,
  clearExternalAttachment
} from '@/chat/chat-store'
import {
  ChatContextAmbientLine,
  ChatContextCapsule,
  ChatReasoningCapsule,
  ChatTemperatureCapsule
} from '@/chat/context-progress-bar'
import { useChatSubmit } from '@/chat/use-chat-submit'
import { useAtomListen } from '@/shared/hooks/use-atom-listen'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { ConversationInput } from './conversation-input'
import { ConversationSurface } from './conversation-surface'
import { useVoiceRecorder } from './use-voice-recorder'

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
  const turnInFlight = useStore($chatTurnInFlight)
  const lastStreaming = useStore($lastAssistantStreaming)
  const pendingBatchLen = useStore($pendingPromptBatch).length
  const dict = useStrings()
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const [externalPaths, setExternalPaths] = useState<string[]>([])
  const [paramsPanelOpen, setParamsPanelOpen] = useState(false)
  const [paramsPanelTab, setParamsPanelTab] = useState<ChatParamsTab>('context')
  const paramsPanelRef = useRef<HTMLDivElement>(null)

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

  const submit = useChatSubmit({
    externalPaths,
    gatewayState,
    isReadOnlySession,
    onClearExternalPaths: () => setExternalPaths([]),
    onPreCheckFail: msg => notify({ kind: 'warning', message: msg })
  })

  const { text, setText, pending, setPending, sending, send, handleStop } = submit
  const { recording, start: startRecording, stop: stopRecording } = useVoiceRecorder({ isReadOnlySession })

  const isGenerating = gatewayState === 'open' && (sending || pendingBatchLen > 0 || turnInFlight || lastStreaming)

  // 外部文件投喂与常规附件合并
  useAtomListen($pendingExternalAttachment, state => {
    if (!state || state.paths.length === 0) {
      return
    }

    setExternalPaths(prev => [...prev, ...state.paths])
    clearExternalAttachment()
    notify({ kind: 'info', message: dict.chat.filesReceived(state.paths.length) })
  })

  // 撤销草稿回填，多窗口按会话过滤
  useAtomListen($chatDraftFromUndo, draft => {
    if (!draft || draft.session_id !== chatSessionId) {
      return
    }

    setText(draft.text)
    $chatDraftFromUndo.set(null)
  })

  const submitState = useMemo(
    () => ({
      externalPaths,
      gatewayState,
      isGenerating,
      isReadOnlySession,
      pending,
      recording,
      sending,
      text
    }),
    [externalPaths, gatewayState, isGenerating, isReadOnlySession, pending, recording, sending, text]
  )

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
        <ConversationInput
          attachMenuOpen={attachMenuOpen}
          externalPaths={externalPaths}
          onAttachMenuToggle={setAttachMenuOpen}
          onDrop={e => {
            const paths = resolveDroppedFiles(e.dataTransfer?.files)

            if (paths.length === 0) {
              return
            }

            e.preventDefault()
            setExternalPaths(prev => [...prev, ...paths])
            notify({ kind: 'info', message: dict.chat.attachmentsAdded(paths.length) })
          }}
          onPaste={async e => {
            const files = Array.from(e.clipboardData?.files ?? [])

            if (files.length === 0) {
              return
            }

            e.preventDefault()

            for (const file of files) {
              if (file.type.startsWith('image/')) {
                const dataUrl = await new Promise<string | null>(resolve => {
                  const reader = new FileReader()

                  reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : null)
                  reader.onerror = () => resolve(null)
                  reader.readAsDataURL(file)
                })

                if (dataUrl) {
                  setPending({ type: 'image', value: dataUrl, fileName: file.name })
                }

                continue
              }

              let filePath = (file as File & { path?: string }).path

              if (!filePath && window.spiritagentWebUtils) {
                try {
                  filePath = window.spiritagentWebUtils.getPathForFile(file)
                } catch {
                  filePath = undefined
                }
              }

              if (filePath) {
                if (window.spiritagent.registerUserSelectedPaths) {
                  await window.spiritagent.registerUserSelectedPaths([filePath]).catch(() => {})
                }

                if (file.type.startsWith('video/')) {
                  await attachVideoFile(filePath, setPending)
                } else {
                  setExternalPaths(prev => [...prev, filePath as string])
                }
              }
            }
          }}
          onRecordingPointerCancel={e => {
            if (e.currentTarget.hasPointerCapture(e.pointerId)) {
              e.currentTarget.releasePointerCapture(e.pointerId)
            }

            void stopRecording()
          }}
          onRecordingPointerDown={e => {
            e.currentTarget.setPointerCapture(e.pointerId)
            void startRecording()
          }}
          onRecordingPointerUp={e => {
            if (e.currentTarget.hasPointerCapture(e.pointerId)) {
              e.currentTarget.releasePointerCapture(e.pointerId)
            }

            void stopRecording()
          }}
          onSend={() => {
            void send()
          }}
          onSetPending={setPending}
          onSetText={setText}
          onStop={handleStop}
          submit={submitState}
          variant={variant}
        />
      </div>
    </div>
  )
}
