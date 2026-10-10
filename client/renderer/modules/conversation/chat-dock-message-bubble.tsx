import { useStore } from '@nanostores/react'
import type React from 'react'
import { memo, useCallback, useEffect, useId, useRef, useState } from 'react'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { useClipboard } from '@/shared/hooks/use-clipboard'
import { ArrowBackUp, ChevronDown, Copy, GitFork, Pencil, RefreshCw, Search } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gatewayState } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { voicePlaybackKey } from '@ipc/contracts'

import { stripAttachmentDirectives } from './chat-display-text'
import { ChatMediaCard } from './chat-media-card'
import { useMessageSessionActions } from './chat-message-session-actions'
import { type ChatMessageBody, type ChatMessageListItem } from './chat-store'
import { ChatVoiceBar } from './chat-voice-bar'
import { CompanionAvatar } from './companion-avatar'
import { formatConversationTime } from './conversation-time'
import { useConversationView } from './conversation-view'
import { MessageContextMenu } from './message-context-menu'
import { ToolChipTimeline } from './tool-chip-timeline'

// 居中的元信息行，而非聊天气泡。Slash 命令结果与历史清空标记（详见 PROTOCOL「Slash 命令」）走同一形态。
const SYSTEM_PILL_SUBTYPES = new Set(['status_cleared', 'status_command_result', 'status_media_failed'])

export type ConversationVariant = 'living' | 'workbench'

interface MessageBubbleProps {
  message: ChatMessageListItem
  showTimeLabel?: boolean
  variant?: ConversationVariant
}

// 异步媒体送达复用此状态行；可见正文由消息投影决定。
const MEDIA_STATUS_SUBTYPE = 'status_media'

function wrapWithTimeDivider(timeDivider: React.ReactNode, node: React.JSX.Element): React.JSX.Element {
  if (!timeDivider) {
    return node
  }

  return (
    <div className="flex flex-col">
      {timeDivider}
      {node}
    </div>
  )
}

function MessageBubbleInner({ message, showTimeLabel, variant }: MessageBubbleProps): React.JSX.Element {
  const { $chatMessageBodies, $chatTurnInFlight } = useConversationView().controller

  // 仅订阅本 id 的 body，避免流式增量触发全局重渲染。
  const bodies = useStore($chatMessageBodies, { keys: [message.id], deps: [message.id] })
  // 撤回在 in-flight 时会被服务端拒绝，必须订这个 atom，否则 memo 挡掉菜单项状态。
  const turnInFlight = useStore($chatTurnInFlight)
  const body: ChatMessageBody | undefined = bodies[message.id]

  if (!body) {
    return <></>
  }

  return (
    <MessageBubbleWithBody
      body={body}
      message={message}
      showTimeLabel={showTimeLabel}
      turnInFlight={turnInFlight}
      variant={variant ?? 'living'}
    />
  )
}

function MessageBubbleWithBody({
  body,
  message,
  showTimeLabel,
  turnInFlight,
  variant
}: {
  body: ChatMessageBody
  message: ChatMessageListItem
  showTimeLabel?: boolean
  turnInFlight: boolean
  variant: ConversationVariant
}): React.JSX.Element {
  const { controller, eligible } = useConversationView()

  const {
    $chatEditDraft,
    $chatSessionKind,
    $chatSessionReadOnly,
    $lastEditableUserMessage,
    $retryableAssistantMessage,
    retryAssistantReply,
    startEditingMessage
  } = controller

  const dict = useStrings()
  const subtype = message.subtype || ''
  const isUser = message.role === 'user'
  const sessionKind = useStore($chatSessionKind)
  const readOnly = useStore($chatSessionReadOnly)
  const editing = useStore($chatEditDraft)
  const lastEditableMessage = useStore($lastEditableUserMessage)
  const retryableMessage = useStore($retryableAssistantMessage)
  const gatewayState = useStore($gatewayState)

  // 右键菜单是纯临时态：局部 useState，不持久化、不入 store；多窗口各自独立。
  const panelActive = usePanelActivity()
  const { copy } = useClipboard()
  const sessionActions = useMessageSessionActions({ messageId: message.id })
  const [summaryExpanded, setSummaryExpanded] = useState(false)
  const [menuPosition, setMenuPosition] = useState<{ x: number; y: number } | null>(null)
  const [menuSelection, setMenuSelection] = useState('')
  const bubbleRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const closeMenu = useCallback((restoreFocus = false): void => {
    setMenuPosition(null)

    if (restoreFocus) {
      bubbleRef.current?.focus({ preventScroll: true })
    }
  }, [])

  useEffect(() => {
    closeMenu()
  }, [eligible, panelActive, message.id, closeMenu])

  const timeLabel = showTimeLabel ? formatConversationTime(message.timestamp) : ''

  const timeDivider = timeLabel ? (
    <div className="flex justify-center select-none py-1">
      <span className="text-[11px] text-faint">{timeLabel}</span>
    </div>
  ) : null

  // 摘要卡只凭 subtype 识别；正文格式不是识别契约，用户或模型写出相同文字仍是普通消息。
  if (subtype === 'compress_summary') {
    // 第一行是胶囊标题，剩余为摘要正文。
    const rawText = body.text
    const newlineIdx = rawText.indexOf('\n')
    let title = ''
    let summary = ''

    if (newlineIdx !== -1) {
      title = rawText.slice(0, newlineIdx).trim()
      summary = rawText.slice(newlineIdx + 1).trim()
    } else {
      const bracketMatch = rawText.match(/^(\[[^\]]+\])\s*([\s\S]*)$/)

      if (bracketMatch) {
        title = bracketMatch[1].trim()
        summary = bracketMatch[2].trim()
      } else {
        title = rawText.length > 30 ? `${rawText.slice(0, 30)}...` : rawText
        summary = rawText
      }
    }

    const cardId = `summary-card-${message.id}`

    return wrapWithTimeDivider(
      timeDivider,
      <div className="relative my-3 flex flex-col items-center gap-2 px-1">
        <div className="flex w-full items-center gap-3">
          <div className="h-px flex-1 bg-line-strong" />
          <button
            aria-controls={cardId}
            aria-expanded={summaryExpanded}
            className={cn(
              'group inline-flex max-w-[80%] items-center gap-1.5 truncate rounded-full border border-line-standard bg-surface-card/80 px-3.5 py-1 text-xs text-muted backdrop-blur-glass transition hover:bg-fill-hover hover:text-strong cursor-pointer',
              'animate-in fade-in zoom-in-95 duration-150'
            )}
            onClick={() => setSummaryExpanded(o => !o)}
            type="button"
          >
            <span className="truncate">{title}</span>
            <ChevronDown
              className={cn('size-3 shrink-0 transition-transform duration-200', summaryExpanded && 'rotate-180')}
            />
          </button>
          <div className="h-px flex-1 bg-line-strong" />
        </div>
        {summaryExpanded && (
          <div
            className="w-[min(560px,calc(100%-2rem))] max-h-80 overflow-y-auto rounded-2xl border border-line-standard bg-surface-card/95 p-3.5 text-[13px] leading-relaxed text-body shadow-lg backdrop-blur-glass animate-in fade-in slide-in-from-top-1 duration-150"
            id={cardId}
          >
            <div className="whitespace-pre-wrap break-words select-text cursor-text">
              {summary || <span className="text-faint">{dict.chat.summary.emptyBody}</span>}
            </div>
          </div>
        )}
      </div>
    )
  }

  if (SYSTEM_PILL_SUBTYPES.has(subtype)) {
    return wrapWithTimeDivider(
      timeDivider,
      <div className="my-1.5 flex justify-center px-2">
        <div className="max-w-[90%] rounded-full border border-line-standard bg-surface-card/60 px-3 py-1 text-center text-xs leading-relaxed text-muted backdrop-blur-glass shadow-xs">
          {body.text}
        </div>
      </div>
    )
  }

  if (subtype === MEDIA_STATUS_SUBTYPE) {
    return wrapWithTimeDivider(
      timeDivider,
      <div className="my-1 flex justify-start px-2">
        <div className="flex max-w-[80%] flex-col gap-1">
          {body.text ? <p className="whitespace-pre-wrap text-sm text-strong">{body.text}</p> : null}
          {body.media?.map(m => (
            <ChatMediaCard item={m} key={m.url} />
          ))}
        </div>
      </div>
    )
  }

  const isVoiceBarMode = variant === 'living' && !isUser && body.replyType === 'voice' && Boolean(body.replyAudio)
  const isVoicePendingOrStreaming = isVoiceBarMode && body.streaming

  // 必须有后端 Message.id 才能回传；回合进行中服务端会拒绝撤回，菜单项一并隐藏。
  const canOperate =
    !readOnly &&
    Boolean(message.backendMessageId) &&
    !editing &&
    !turnInFlight &&
    !body.streaming &&
    !body.error &&
    !body.cancelled &&
    !body.toolName

  // 生活空间只有唯一陪伴上下文，不允许派生。
  const canFork = variant === 'workbench' && sessionKind === 'standard' && canOperate

  // 避免误点助手行变成撤回伙伴上一句。
  const canUndo = isUser && sessionKind === 'standard' && canOperate
  const canEdit = isUser && canOperate && lastEditableMessage?.id === message.id

  // 用户附件渲染为可点击图片卡；正文剔除附件指令行，纯图片消息不渲染空气泡。
  const visibleText = isUser ? stripAttachmentDirectives(body.text) : body.text
  // 流式追加时去除前导空行防撑大气泡上方，非流式时去除首尾空白、保留内部段落。
  const displayText = body.streaming ? visibleText.trimStart() : visibleText.trim()
  const hideTextBubble = Boolean(body.replyMedia) || (isUser && !displayText && Boolean(body.attachments?.length))
  const tools = body.tools?.length ? body.tools : body.toolName ? [body.toolName] : []
  const toolOnly = tools.length > 0 && !displayText && !body.error && !body.cancelled
  const showToolIndicator = !isUser && tools.length > 0
  const showLivingToolWait = variant === 'living' && toolOnly && Boolean(body.streaming)

  // 生活空间不暴露工具轨迹，但回合进行中仍保留通用等待气泡，避免长时间准备期只剩用户消息。
  if (variant === 'living' && toolOnly && !showLivingToolWait) {
    return <></>
  }

  const hasVisibleReasoning = variant === 'workbench' && Boolean(body.reasoning?.trim())

  if (
    !isUser &&
    !displayText &&
    !body.streaming &&
    !body.attachments?.length &&
    !body.media?.length &&
    !body.replyMedia &&
    !body.error &&
    !body.cancelled &&
    !hasVisibleReasoning
  ) {
    return <></>
  }

  // 只要消息具有非空可见正文且非流式传输中，即允许一键复制
  const canCopy = Boolean(displayText) && !body.streaming && !isVoicePendingOrStreaming
  const sourceMessageId = message.backendMessageId

  const copyToClipboard = async (text: string): Promise<void> => {
    try {
      await copy(text)
    } catch (err) {
      notifyError(err, dict.chat.copy.failed)
    }
  }

  // 右键时刻捕获选区：随后点击菜单项会清掉选区；仅当选区与本气泡相交才提供"复制所选"。
  const handleContextMenu = (event: React.MouseEvent<HTMLDivElement>): void => {
    event.preventDefault()
    event.stopPropagation()

    if (!eligible || !panelActive) {
      return
    }

    const selection = window.getSelection()
    const rawSelection = selection?.toString().trim() ?? ''

    const selectionText = rawSelection && selection?.containsNode(event.currentTarget, true) ? rawSelection : ''

    // 按消息可用性装配菜单项；一项都没有（如纯流式等待）时只拦截原生菜单，不弹菜单。
    if (!selectionText && !canCopy && !canEdit && !canUndo && !canFork) {
      return
    }

    setMenuSelection(selectionText)
    setMenuPosition({ x: event.clientX, y: event.clientY })
  }

  return wrapWithTimeDivider(
    timeDivider,
    <div className={cn('relative flex shrink-0 gap-2.5 overflow-visible', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && variant === 'workbench' && <CompanionAvatar />}
      <div className="relative flex max-w-[80%] overflow-visible">
        <div className={cn('flex min-w-0 flex-col', isUser ? 'items-end' : 'items-start')}>
          {body.attachments?.length ? (
            <div className="flex flex-col gap-1">
              {body.attachments.map(a => (
                <ChatMediaCard item={{ type: a.type, url: a.url }} key={a.url} />
              ))}
            </div>
          ) : null}
          {showToolIndicator && variant === 'workbench' ? <ToolChipTimeline active={toolOnly} tools={tools} /> : null}
          {isVoiceBarMode ? (
            isVoicePendingOrStreaming ? (
              <div className="relative select-text cursor-text whitespace-pre-wrap break-words rounded-2xl px-4 py-2.5 text-xs leading-relaxed shadow-xs backdrop-blur-md border border-line-standard bg-surface-card text-strong">
                <span className="animate-pulse text-faint">{dict.chat.typing}</span>
              </div>
            ) : (
              <ChatVoiceBar
                duration={body.replyAudio?.duration}
                messageId={message.id}
                playbackKey={
                  message.backendMessageId !== undefined && body.replyIndex !== undefined
                    ? voicePlaybackKey(message.backendMessageId, body.replyIndex)
                    : undefined
                }
                text={displayText}
              />
            )
          ) : !hideTextBubble && (!toolOnly || showLivingToolWait) ? (
            <div
              className={cn(
                'relative select-text cursor-text whitespace-pre-wrap break-words rounded-2xl px-4 py-2.5 text-xs leading-relaxed shadow-sm',
                isUser
                  ? 'border border-accent-line/45 text-strong shadow-[inset_0_1px_0.5px_rgba(255,255,255,0.35)] backdrop-blur-xl backdrop-saturate-180'
                  : 'border border-line-standard bg-surface-card text-strong shadow-xs backdrop-blur-md'
              )}
              onContextMenu={handleContextMenu}
              ref={bubbleRef}
              style={
                isUser
                  ? {
                      backgroundColor: 'var(--ui-bubble-user-bg, color-mix(in srgb, var(--ui-accent) 20%, transparent))'
                    }
                  : undefined
              }
              tabIndex={-1}
            >
              {body.error ? (
                <span className="flex flex-wrap items-center gap-x-3 gap-y-2 text-amber-500">
                  <span>{body.error}</span>
                  {retryableMessage?.id === message.id && (
                    <button
                      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line-standard px-2 py-1 text-strong transition hover:bg-fill-hover disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={gatewayState !== 'open'}
                      onClick={() => void retryAssistantReply(message.id)}
                      type="button"
                    >
                      <RefreshCw className="size-3.5" />
                      {dict.common.retry}
                    </button>
                  )}
                </span>
              ) : body.cancelled ? (
                <span className="text-muted">{dict.chat.summary.cancelled}</span>
              ) : displayText ? (
                <>
                  {displayText}
                  {body.streaming && <span className="animate-caret-pulse" />}
                </>
              ) : (
                <span className="animate-pulse text-faint">{variant === 'living' ? dict.chat.typing : '…'}</span>
              )}
            </div>
          ) : null}
          {!isUser && variant === 'workbench' && (body.reasoning || (body.streaming && !displayText)) ? (
            <ReasoningBlock reasoning={body.reasoning} streaming={body.streaming} />
          ) : null}
          {body.replyMedia && body.replyMedia.status !== 'ready' ? (
            <div
              className="rounded-2xl border border-line-standard bg-surface-card px-4 py-3 text-sm text-muted"
              role="status"
            >
              {body.replyMedia.status === 'pending'
                ? dict.chat.media.generating
                : body.replyMedia.error ||
                  (body.replyMedia.status === 'result_unknown'
                    ? dict.chat.media.resultUnknown
                    : dict.chat.media.generationFailed)}
            </div>
          ) : null}
          {body.media?.length ? (
            <div className="mt-1 flex flex-col gap-1">
              {body.media.map(m => (
                <ChatMediaCard item={m} key={m.url} />
              ))}
            </div>
          ) : null}
        </div>
        {menuPosition && eligible && panelActive ? (
          <MessageContextMenu
            id={menuId}
            items={[
              ...(menuSelection
                ? [
                    {
                      icon: Copy,
                      label: dict.chat.contextMenu.copySelection,
                      onSelect: () => void copyToClipboard(menuSelection)
                    }
                  ]
                : []),
              ...(canCopy
                ? [
                    {
                      icon: Copy,
                      label: dict.chat.copy.label,
                      onSelect: () => void copyToClipboard(displayText)
                    }
                  ]
                : []),
              ...(canEdit
                ? [{ icon: Pencil, label: dict.chat.edit.label, onSelect: () => startEditingMessage(message.id) }]
                : []),
              ...(canUndo && sourceMessageId !== undefined
                ? [
                    {
                      disabled: sessionActions.undo.disabled,
                      icon: ArrowBackUp,
                      label: sessionActions.undo.label,
                      onSelect: () => void sessionActions.runUndo(sourceMessageId)
                    }
                  ]
                : []),
              ...(canFork && sourceMessageId !== undefined
                ? [
                    {
                      disabled: sessionActions.fork.disabled,
                      icon: GitFork,
                      label: sessionActions.fork.label,
                      onSelect: () => void sessionActions.runFork(sourceMessageId)
                    }
                  ]
                : [])
            ]}
            label={dict.chat.contextMenu.label}
            onClose={closeMenu}
            position={menuPosition}
          />
        ) : null}
      </div>
    </div>
  )
}

function ReasoningBlock({
  reasoning,
  streaming
}: {
  reasoning?: string
  streaming?: boolean
}): React.JSX.Element | null {
  const dict = useStrings()
  const [expanded, setExpanded] = useState(false)
  const trimmed = reasoning?.trim() ?? ''

  if (!trimmed && !streaming) {
    return null
  }

  return (
    <div className="mt-1.5 flex max-w-full flex-col select-none">
      <button
        aria-expanded={expanded}
        className={cn(
          'group/reason inline-flex items-center gap-1.5 rounded-lg border border-line-standard bg-fill-faint px-2.5 py-1 text-[11px] text-muted backdrop-blur-xs transition-all duration-150',
          'hover:border-line-strong hover:bg-fill-hover hover:text-strong cursor-pointer text-left'
        )}
        onClick={() => setExpanded(prev => !prev)}
        type="button"
      >
        <Search className="size-3 shrink-0 text-faint group-hover/reason:text-muted transition-colors" />
        <span className="font-medium">
          {streaming && !trimmed
            ? dict.chat.reasoning.thinkingStreaming
            : streaming
              ? dict.chat.reasoning.reasoningStreaming
              : dict.chat.reasoning.done}
        </span>
        {streaming ? <span className="size-1.5 animate-pulse rounded-full bg-blue-400" /> : null}
        <span className="text-[10px] text-faint group-hover/reason:text-muted transition-colors ml-0.5">
          {expanded ? dict.chat.reasoning.collapse : dict.chat.reasoning.expand}
        </span>
        <ChevronDown
          className={cn(
            'size-3 shrink-0 text-faint transition-transform duration-200 group-hover/reason:text-muted',
            expanded ? 'rotate-180' : '-rotate-90'
          )}
        />
      </button>
      {expanded ? (
        <div className="mt-1.5 max-h-60 max-w-full overflow-y-auto rounded-xl border border-line-standard bg-surface-card p-3 text-[11px] leading-relaxed text-body shadow-inner backdrop-blur-md select-text cursor-text whitespace-pre-wrap break-words font-sans">
          {trimmed ? trimmed : <span className="text-faint italic">{dict.chat.reasoning.thinkingStreaming}</span>}
        </div>
      ) : null}
    </div>
  )
}

// React.memo 保证历史气泡不会随 ConversationSurface 的其他状态变化重渲染。
export const MessageBubble = memo(MessageBubbleInner)
