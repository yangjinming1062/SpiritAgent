import { useStore } from '@nanostores/react'
import type React from 'react'
import { memo, useState } from 'react'

import { ChevronDown, Search } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { presentationPorts } from '@/shared/presentation-ports'
import { useStrings } from '@/shared/strings'

import { speechText } from '../../../shared/speech-text'

import { ChatMediaCard } from './chat-media-card'
import { ChatMessageCopyButton } from './chat-message-copy-button'
import { ChatMessageForkButton } from './chat-message-fork-button'
import { ChatMessagePlayButton } from './chat-message-play-button'
import { ChatMessageUndoButton } from './chat-message-undo-button'
import { $chatMessageBodies, $chatTurnInFlight, type ChatMessageBody, type ChatMessageListItem } from './chat-store'
import { ChatVoiceBar, TranscriptBlock } from './chat-voice-bar'
import { formatConversationTime } from './conversation-time'
import { ToolChipTimeline } from './tool-chip-timeline'

// 居中的元信息行，而非聊天气泡。Slash 命令结果与历史清空标记（详见 PROTOCOL §1.9）走同一形态。
const SYSTEM_PILL_SUBTYPES = new Set(['status_cleared', 'status_command_result'])

// 对话摘要与上下文压缩检查点：居中的分界线式可折叠卡片，默认折叠、点击展开摘要全文。
// 走独立分支而不是 pill —— 视觉权重要让用户意识到这是个有信息量的节点。
const SUMMARY_CARD_SUBTYPES = new Set(['compress_summary', 'daily_summary'])

export type ConversationVariant = 'living' | 'workbench'

interface MessageBubbleProps {
  message: ChatMessageListItem
  showTimeLabel?: boolean
  variant?: ConversationVariant
}

// 戳 / 拖拽追踪：侧对齐但视觉上弱化。
const STATUS_TRACE_SUBTYPES = new Set(['status_interaction', 'status_reaction'])
// 后台视频完成的送达行：正文是给 LLM 的摘要，渲染端只显示媒体卡。
const MEDIA_STATUS_SUBTYPE = 'status_media'

// 路径模式降级时的 @file: 指令（含历史行里拼进正文的 @file:data:,... 长串）只服务 LLM，
// 不进用户可见正文；逐行剔除而非整段正则，避免误伤正文里的普通 @ 提及。
function stripAttachmentDirectives(text: string): string {
  return text
    .split('\n')
    .filter(line => !/^@file:/i.test(line.trim()))
    .join('\n')
}

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
  // 仅订阅本 id 的 body，避免流式增量触发全局重渲染。
  const bodies = useStore($chatMessageBodies, { keys: [message.id], deps: [message.id] })
  // 撤回在 in-flight 时会被服务端拒绝，必须订这个 atom，否则 memo 挡掉按钮显隐。
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
  const dict = useStrings()
  const subtype = message.subtype || ''
  const isUser = message.role === 'user'
  const ports = presentationPorts()
  const portraitUrl = useStore(ports.$portraitUrl)
  const activeAvatarId = useStore(ports.$activeAvatarId)
  const responseMode = useStore(ports.$responseMode)

  // 摘要/压缩卡片折叠态：组件局部 useState，默认折叠，不持久化、不入 store；多窗口各自独立展开。
  const [summaryExpanded, setSummaryExpanded] = useState(false)

  const timeLabel = showTimeLabel ? formatConversationTime(message.timestamp) : ''

  const timeDivider = timeLabel ? (
    <div className="flex justify-center select-none py-1">
      <span className="text-[11px] text-faint">{timeLabel}</span>
    </div>
  ) : null

  const isSummaryCard =
    SUMMARY_CARD_SUBTYPES.has(subtype) ||
    (!subtype && (body.text.startsWith('[📝 截至') || body.text.startsWith('[🗜️ 对话压缩')))

  if (isSummaryCard) {
    // 解析 content：第一行（如 "[📝 截至 ...]" 或 "[🗜️ 对话压缩 — ...]"）是胶囊标题，剩余为摘要 body。
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

  if (STATUS_TRACE_SUBTYPES.has(subtype)) {
    return wrapWithTimeDivider(
      timeDivider,
      <div className={`my-0.5 flex ${isUser ? 'justify-end' : 'justify-start'} px-2`}>
        <div className="max-w-[80%] text-[11px] italic text-faint">{body.text}</div>
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

  const hasSpeech = Boolean(speechText(body.text))

  const isVoiceBarMode =
    variant === 'living' &&
    !isUser &&
    responseMode === 'voice' &&
    !body.error &&
    !body.cancelled &&
    !body.toolName &&
    body.voiceStatus !== 'failed' &&
    (body.streaming || hasSpeech)

  const isVoicePendingOrStreaming = isVoiceBarMode && (body.streaming || body.voiceStatus === 'pending')

  // 仅在生活空间、默认文字模式、已完成、非错误、非取消且有文本的助手消息上显示播放按钮。工作台侧重干活直接看文本。
  const showPlayButton =
    variant === 'living' &&
    !isUser &&
    !isVoiceBarMode &&
    !body.streaming &&
    hasSpeech &&
    !body.error &&
    !body.cancelled &&
    !body.toolName

  // 必须有后端 Message.id 才能回传；回合进行中服务端会拒绝撤回，按钮一并藏掉。
  const canOperate =
    Boolean(message.backendMessageId) &&
    !turnInFlight &&
    !body.streaming &&
    !body.error &&
    !body.cancelled &&
    !body.toolName

  // 生活空间只有唯一陪伴上下文，不允许派生。
  const canFork = variant === 'workbench' && canOperate

  // 避免误点助手行变成撤回伙伴上一句。
  const canUndo = isUser && canOperate

  // 用户附件渲染为可点击图片卡（data URL 或本地路径，媒体源通道负责取图）；
  // 正文剔除 @file: 指令行，纯图片消息不渲染空气泡。
  const visibleText = isUser ? stripAttachmentDirectives(body.text) : body.text
  // 规整展示文本：流式追加时去除前导空行防撑大气泡上方，非流式时去除首尾多余空白，保留内部段落与换行。
  const displayText = body.streaming ? visibleText.trimStart() : visibleText.trim()
  const hideTextBubble = isUser && !displayText && Boolean(body.attachments?.length)
  const tools = body.tools?.length ? body.tools : body.toolName ? [body.toolName] : []
  const toolOnly = tools.length > 0 && !displayText && !body.error && !body.cancelled
  const showToolIndicator = !isUser && tools.length > 0

  // 纯工具中间帧在生活空间不占位渲染
  if (variant === 'living' && toolOnly) {
    return <></>
  }

  const hasVisibleReasoning = variant === 'workbench' && Boolean(body.reasoning?.trim())

  // 非流式、非错误且无任何可见文本与媒体及推理的空消息不渲染
  if (
    !isUser &&
    !displayText &&
    !body.streaming &&
    !body.attachments?.length &&
    !body.media?.length &&
    !body.error &&
    !body.cancelled &&
    !hasVisibleReasoning
  ) {
    return <></>
  }

  // 只要消息具有非空可见正文且非流式传输中，即允许一键复制
  const canCopy = Boolean(displayText) && !body.streaming && !isVoicePendingOrStreaming
  const hasActions = canFork || canUndo || canCopy

  return wrapWithTimeDivider(
    timeDivider,
    <div className={cn('relative flex shrink-0 gap-2.5 overflow-visible', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && variant === 'workbench' && (
        <div className="size-8 shrink-0 overflow-hidden rounded-full border border-white/15 bg-white/10 shadow-sm mt-0.5">
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
        className={cn(
          'group/message relative flex max-w-[80%] items-center gap-1.5 overflow-visible',
          isUser ? 'flex-row-reverse' : 'flex-row'
        )}
      >
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
              <>
                <ChatVoiceBar duration={body.voiceDuration} messageId={message.id} text={displayText} />
                <TranscriptBlock text={displayText} />
              </>
            )
          ) : !hideTextBubble && !toolOnly ? (
            <div
              className={cn(
                'relative select-text cursor-text whitespace-pre-wrap break-words rounded-2xl px-4 py-2.5 text-xs leading-relaxed shadow-sm',
                isUser
                  ? 'border border-accent-line/45 text-strong shadow-[inset_0_1px_0.5px_rgba(255,255,255,0.35)] backdrop-blur-xl backdrop-saturate-180'
                  : 'border border-line-standard bg-surface-card text-strong shadow-xs backdrop-blur-md'
              )}
              style={
                isUser
                  ? {
                      backgroundColor: 'var(--ui-bubble-user-bg, color-mix(in srgb, var(--ui-accent) 20%, transparent))'
                    }
                  : undefined
              }
            >
              {body.error ? (
                <span className="text-amber-500">{body.error}</span>
              ) : body.cancelled ? (
                <span className="text-muted">{dict.chat.summary.cancelled}</span>
              ) : displayText ? (
                <>
                  {displayText}
                  {body.streaming && <span className="animate-caret-pulse" />}
                  {showPlayButton && (
                    <span className="ml-1.5 inline-flex align-middle select-none">
                      <ChatMessagePlayButton messageId={message.id} text={body.text} />
                    </span>
                  )}
                </>
              ) : (
                <span className="animate-pulse text-faint">{variant === 'living' ? dict.chat.typing : '…'}</span>
              )}
            </div>
          ) : null}
          {!isUser && variant === 'workbench' && (body.reasoning || (body.streaming && !displayText)) ? (
            <ReasoningBlock reasoning={body.reasoning} streaming={body.streaming} />
          ) : null}
          {body.media?.length ? (
            <div className="mt-1 flex flex-col gap-1">
              {body.media.map(m => (
                <ChatMediaCard item={m} key={m.url} />
              ))}
            </div>
          ) : null}
        </div>
        {hasActions && (
          <MessageActionCluster
            canCopy={canCopy}
            canFork={canFork}
            canUndo={canUndo}
            copyText={displayText}
            messageId={message.id}
            sourceMessageId={message.backendMessageId}
          />
        )}
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

function MessageActionCluster({
  canCopy,
  canFork,
  canUndo,
  copyText,
  messageId,
  sourceMessageId
}: {
  canCopy: boolean
  canFork: boolean
  canUndo: boolean
  copyText?: string
  messageId: string
  sourceMessageId?: number
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'flex shrink-0 items-center gap-0.5 rounded-full border border-line-hairline/80 bg-surface-card/90 p-0.5 shadow-sm backdrop-blur-md',
        'transition-opacity duration-150 select-none',
        'pointer-events-none opacity-0 group-hover/message:pointer-events-auto group-hover/message:opacity-100',
        'focus-within:pointer-events-auto focus-within:opacity-100',
        'has-[[data-busy]]:pointer-events-auto has-[[data-busy]]:opacity-100'
      )}
    >
      {canCopy && copyText && <ChatMessageCopyButton text={copyText} />}
      {canFork && sourceMessageId !== undefined && (
        <ChatMessageForkButton messageId={messageId} sourceMessageId={sourceMessageId} />
      )}
      {canUndo && sourceMessageId !== undefined && (
        <ChatMessageUndoButton messageId={messageId} sourceMessageId={sourceMessageId} />
      )}
    </div>
  )
}

// React.memo 保证历史气泡不会随 ChatDock 的内部状态变化重渲染。
export const MessageBubble = memo(MessageBubbleInner)
