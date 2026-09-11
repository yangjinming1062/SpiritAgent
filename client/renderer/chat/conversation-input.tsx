// 对话输入胶囊：两个入口共用。空胶囊形态；聚焦或挂附件时由 caller 决定是否展开指挥台。
//
// 此组件是受控组件：父组件持有 text/pending/sending/recording 等状态，
// 这里只渲染 + 把事件转回父组件。这样 living / workbench 可以共用同一个
// 视觉与交互壳，而父组件可以各自选择是否挂语音条、附件槽、slash popover。

import { useStore } from '@nanostores/react'
import type React from 'react'
import {
  type ClipboardEvent,
  type Dispatch,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
  type SetStateAction,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react'

import { attachVideoFile, pickFile, pickFolder, pickImage, pickVideo } from '@/chat/chat-attach-picker'
import type { ConversationVariant } from '@/chat/chat-dock-message-bubble'
import { PendingAttachmentView } from '@/chat/chat-pending-attachment'
import { type PendingAttachment, schedulePendingFlush } from '@/chat/chat-store'
import { SlashCommandPopover } from '@/chat/slash-command-popover'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { FileText, FolderOpen, ImageIcon, Mic, Plus, Send, Slash, SquareFilled, Video } from '@/shared/lib/icons'
import {
  $slashCommandMeta,
  fetchSlashCommandMeta,
  fuzzyFilterCommands,
  type ScoredSlashCommand,
  type SlashCommandMeta
} from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

export interface ChatSubmitState {
  externalPaths: string[]
  gatewayState: ConnectionState
  isGenerating: boolean
  isReadOnlySession: boolean
  pending: PendingAttachment | null
  recording: boolean
  sending: boolean
  text: string
}

export interface SlashState {
  highlightIndex?: number
  items?: ScoredSlashCommand[]
  onHighlight?: (index: number) => void
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => void
  onSelect?: (cmd: SlashCommandMeta, args: string[]) => void
  popoverOpen?: boolean
  query?: string
}

export interface ConversationInputProps {
  attachMenuOpen?: boolean
  externalPaths: string[]
  onAttachMenuToggle?: Dispatch<SetStateAction<boolean>>
  onDrop?: (e: React.DragEvent) => void
  onPaste?: (e: ClipboardEvent) => void | Promise<void>
  onRecordingPointerCancel?: (e: PointerEvent<HTMLButtonElement>) => void
  onRecordingPointerDown?: (e: PointerEvent<HTMLButtonElement>) => void
  onRecordingPointerUp?: (e: PointerEvent<HTMLButtonElement>) => void
  onSend: () => void
  onSetPending: Dispatch<SetStateAction<PendingAttachment | null>>
  onSetText: (next: string) => void
  onStop: () => void
  slash?: SlashState
  submit: ChatSubmitState
  variant?: ConversationVariant
}

// 工作台指挥台的展开阈值：超过这个长度、存在附件或聚焦时，长成 2–4 行 textarea。
// 生活空间始终走单行胶囊。
const COMMAND_LINE_THRESHOLD = 80

export function ConversationInput(props: ConversationInputProps): React.JSX.Element {
  const {
    attachMenuOpen = false,
    externalPaths,
    onAttachMenuToggle,
    onDrop,
    onPaste,
    onRecordingPointerCancel,
    onRecordingPointerDown,
    onRecordingPointerUp,
    onSend,
    onSetPending,
    onSetText,
    onStop,
    slash,
    submit,
    variant = 'living'
  } = props

  const { gatewayState, isGenerating, isReadOnlySession, pending, recording, sending, text } = submit

  const {
    highlightIndex: slashHighlightIndex,
    items: slashItems,
    onHighlight: onSlashHighlight,
    onKeyDown: onSlashKeyDown,
    onSelect: onSlashSelect,
    popoverOpen: slashPopoverOpen,
    query: slashQuery
  } = slash ?? {}

  const [internalSlashDismissed, setInternalSlashDismissed] = useState(false)
  const [internalHighlightIndex, setInternalHighlightIndex] = useState(0)
  const [focused, setFocused] = useState(false)
  const [slashPaletteForced, setSlashPaletteForced] = useState(false)

  const dict = useStrings()
  const slashMeta = useStore($slashCommandMeta)

  // 工作台只在「要打字了」时升格成指挥台——空闲保持胶囊形态。
  const expanded = variant === 'workbench' && (focused || Boolean(pending) || text.length >= COMMAND_LINE_THRESHOLD)

  const editorRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null)

  // 升格后把焦点同步进 textarea，避免升格瞬间丢失焦点。
  useEffect(() => {
    if (expanded && editorRef.current && document.activeElement !== editorRef.current) {
      editorRef.current.focus()
      const len = editorRef.current.value.length
      editorRef.current.setSelectionRange(len, len)
    }
  }, [expanded])

  // 仅前导 / 且尚未键入参数时，空 query 仍算命令模式，弹层展示全量。
  const slashContext = useMemo<{ active: boolean; query: string }>(() => {
    if (slashQuery !== undefined || slashPaletteForced) {
      return { active: true, query: slashQuery ?? '' }
    }

    const trimmed = text.trim()

    if (!trimmed.startsWith('/')) {
      return { active: false, query: '' }
    }

    const body = trimmed.slice(1)
    const spaceIdx = body.search(/\s/)

    if (spaceIdx !== -1) {
      return { active: false, query: '' }
    }

    return { active: true, query: body }
  }, [slashPaletteForced, slashQuery, text])

  const items = slashItems ?? (slashContext.active ? fuzzyFilterCommands(slashContext.query, 8) : [])
  const isOpen = (slashPopoverOpen ?? (slashContext.active && !internalSlashDismissed)) && items.length > 0

  useEffect(() => {
    if (!slashContext.active || slashMeta.length > 0) {
      return
    }

    void fetchSlashCommandMeta()
  }, [slashContext.active, slashMeta.length])

  const highlightIdx = slashHighlightIndex ?? internalHighlightIndex

  const setHighlight = (next: number): void => {
    setInternalHighlightIndex(next)
    onSlashHighlight?.(next)
  }

  const handleSlashSelect = (cmd: SlashCommandMeta): void => {
    setSlashPaletteForced(false)

    if (onSlashSelect) {
      onSlashSelect(cmd, [])
    } else {
      onSetText(`/${cmd.name} `)
      editorRef.current?.focus()
    }

    setInternalSlashDismissed(true)
  }

  const showStop = isGenerating && !text.trim() && !pending && externalPaths.length === 0

  const sendDisabled =
    isReadOnlySession ||
    (!showStop &&
      (sending ||
        gatewayState !== 'open' ||
        (!text.trim() && !pending && externalPaths.length === 0) ||
        (pending?.type === 'video' && pending.status !== 'ready')))

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    schedulePendingFlush()
    setSlashPaletteForced(false)
    setInternalSlashDismissed(false)
    setInternalHighlightIndex(0)
    onSetText(e.target.value)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    if (e.nativeEvent.isComposing || e.key.length === 1 || e.key === 'Backspace' || e.key === 'Delete') {
      schedulePendingFlush()
    }

    onSlashKeyDown?.(e)

    if (isOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setHighlight((highlightIdx + 1) % items.length)

        return
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setHighlight((highlightIdx - 1 + items.length) % items.length)

        return
      }

      if (e.key === 'Tab') {
        e.preventDefault()
        const chosen = items[highlightIdx]

        if (chosen) {
          handleSlashSelect(chosen.cmd)
        }

        return
      }

      if (e.key === 'Escape') {
        e.preventDefault()
        setInternalSlashDismissed(true)

        return
      }

      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        const chosen = items[highlightIdx]

        if (chosen) {
          e.preventDefault()
          handleSlashSelect(chosen.cmd)

          return
        }
      }
    }

    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()

      if (text.trim() === '/') {
        return
      }

      onSend()
    }
  }

  const commonEditorProps = {
    disabled: isReadOnlySession,
    onBlur: () => setFocused(false),
    onChange: handleChange,
    onCompositionUpdate: () => schedulePendingFlush(),
    onFocus: () => setFocused(true),
    onKeyDown: handleKeyDown,
    onPaste,
    placeholder: variant === 'workbench' ? dict.chat.input.workbenchPlaceholder : dict.chat.inputPlaceholder,
    value: text
  }

  // 工作台指挥台：textarea；生活空间：单行 input。
  const editorElement = expanded ? (
    <textarea
      {...commonEditorProps}
      className="w-full flex-1 resize-none bg-transparent border-0 outline-none text-xs text-strong placeholder:text-faint px-1.5 py-1.5 min-h-[2.4em] max-h-[7.2em] leading-snug"
      ref={editorRef as unknown as RefObject<HTMLTextAreaElement>}
      rows={Math.min(4, Math.max(2, Math.ceil((text.match(/\n/g)?.length ?? 0) + 1)))}
    />
  ) : (
    <input
      {...commonEditorProps}
      className="h-full flex-1 bg-transparent border-0 outline-none text-xs px-1.5 text-strong placeholder:text-faint"
      ref={editorRef as unknown as RefObject<HTMLInputElement>}
      type="text"
    />
  )

  return (
    <div
      className={cn(
        'flex flex-col gap-2 shrink-0 transition-all',
        variant === 'workbench'
          ? 'border-0 bg-transparent p-0'
          : 'w-full max-w-2xl mx-auto rounded-2xl border border-line-standard bg-surface-card shadow-lg backdrop-blur-xl p-2.5'
      )}
      onDragOver={onDrop ? e => e.preventDefault() : undefined}
      onDrop={
        onDrop
          ? e => {
              // 阻止冒泡：whisper-overlay 等父容器同时挂着 onDrop 接收整层浮层的拖入，
              // 这里消费后不必再交给外层，否则文件路径会重复入附件。
              e.stopPropagation()
              onDrop(e)
            }
          : undefined
      }
    >
      {isReadOnlySession && <p className="text-center text-[10px] text-faint">{dict.chat.input.readOnlyHint}</p>}

      {pending && (
        <div className="px-1">
          <PendingAttachmentView
            onRemove={() => onSetPending(null)}
            onRetry={pending.type === 'video' ? () => void attachVideoFile(pending.path, onSetPending) : undefined}
            pending={pending}
            sending={sending}
          />
        </div>
      )}

      <div
        className={cn(
          'relative flex w-full items-center px-3 transition',
          variant === 'living'
            ? cn(
                'border border-line-hairline bg-fill-faint focus-within:border-accent focus-within:bg-fill-hover/40 shadow-none',
                expanded ? 'rounded-2xl min-h-[3.6em] py-1.5' : 'rounded-xl min-h-[2.6em] py-1'
              )
            : cn(
                'rounded-2xl border border-line-standard bg-surface-card/80 backdrop-blur-xl focus-within:border-accent-line focus-within:bg-surface-card shadow-[inset_0_1px_1px_rgba(255,255,255,0.45)]',
                expanded ? 'min-h-[3.6em] py-1.5' : 'min-h-[2.6em] py-1'
              )
        )}
      >
        {isOpen && (
          <SlashCommandPopover
            highlightedIndex={highlightIdx}
            onHighlight={idx => {
              setInternalHighlightIndex(idx)
              onSlashHighlight?.(idx)
            }}
            onSelect={cmd => handleSlashSelect(cmd)}
            query={slashContext.query}
          />
        )}

        {editorElement}
      </div>

      <div className="flex items-center justify-between gap-1.5">
        <div className="flex items-center gap-1.5">
          <div className="relative shrink-0">
            <button
              aria-label={dict.chat.input.addAttachment}
              className={cn(
                'inline-flex size-7 items-center justify-center rounded-full border border-line-hairline bg-fill-faint text-muted transition hover:border-line-standard hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40',
                attachMenuOpen && 'border-line-strong bg-fill-hover text-strong'
              )}
              disabled={isReadOnlySession}
              onClick={() => onAttachMenuToggle?.(!attachMenuOpen)}
              title={dict.chat.input.addAttachment}
              type="button"
            >
              <Plus className="size-4" />
            </button>

            {attachMenuOpen && (
              <div className="absolute bottom-full mb-2 left-0 z-50 flex w-36 flex-col gap-0.5 rounded-xl border border-line-standard bg-surface-card p-1 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150">
                <button
                  className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                  onClick={() => void pickFile(onSetPending)}
                  type="button"
                >
                  <FileText className="size-3.5 text-accent" />
                  <span>{dict.chat.input.addFile}</span>
                </button>
                <button
                  className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                  onClick={() => void pickFolder(onSetPending)}
                  type="button"
                >
                  <FolderOpen className="size-3.5 text-amber-400" />
                  <span>{dict.chat.input.addFolder}</span>
                </button>
                <button
                  className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                  onClick={() => void pickImage(onSetPending)}
                  type="button"
                >
                  <ImageIcon className="size-3.5 text-emerald-400" />
                  <span>{dict.chat.input.addImage}</span>
                </button>
                <button
                  className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                  onClick={() => void pickVideo(onSetPending)}
                  type="button"
                >
                  <Video className="size-3.5 text-rose-400" />
                  <span>{dict.chat.input.addVideo}</span>
                </button>
              </div>
            )}
          </div>

          <div className="relative shrink-0">
            <button
              aria-label={dict.chat.input.slashShortcut}
              className={cn(
                'inline-flex size-7 items-center justify-center rounded-full border border-line-hairline bg-fill-faint text-muted transition hover:border-accent-line/60 hover:bg-accent-soft hover:text-accent disabled:pointer-events-none disabled:opacity-40',
                isOpen && 'border-accent-line bg-accent-soft text-accent'
              )}
              disabled={isReadOnlySession}
              onClick={() => {
                onAttachMenuToggle?.(false)

                if (isOpen) {
                  setSlashPaletteForced(false)
                  setInternalSlashDismissed(true)

                  return
                }

                setSlashPaletteForced(true)
                setInternalSlashDismissed(false)

                if (!text.trim()) {
                  onSetText('/')
                }

                editorRef.current?.focus()
              }}
              title={dict.chat.input.slashShortcutHint}
              type="button"
            >
              <Slash className="size-4" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <button
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-full border border-line-hairline bg-fill-faint text-muted transition hover:border-line-standard hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40',
              recording && 'border-rose-400/70 bg-rose-500/25 text-rose-300 animate-pulse'
            )}
            disabled={isReadOnlySession}
            onPointerCancel={onRecordingPointerCancel}
            onPointerDown={onRecordingPointerDown}
            onPointerUp={onRecordingPointerUp}
            title={recording ? dict.chat.input.releaseToSendVoice : dict.chat.input.pressToRecordVoice}
            type="button"
          >
            <Mic className="size-3.5" />
          </button>

          <button
            aria-label={showStop ? dict.chat.input.stopGenerating : dict.chat.input.sendMessage}
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-xl transition disabled:pointer-events-none disabled:opacity-30',
              showStop
                ? 'bg-rose-500/90 hover:bg-rose-600 text-white shadow-xs'
                : 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_12px_rgba(37,99,235,0.6)]'
            )}
            disabled={sendDisabled}
            onClick={() => void (showStop ? onStop() : onSend())}
            title={showStop ? dict.chat.input.stopGenerating : dict.chat.input.sendMessageShortcut}
            type="button"
          >
            {showStop ? <SquareFilled className="size-3" /> : <Send className="size-3.5 -rotate-12" />}
          </button>
        </div>
      </div>
    </div>
  )
}
