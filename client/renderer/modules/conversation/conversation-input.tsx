import { useStore } from '@nanostores/react'
// 对话输入胶囊：生活空间、工作台与轻语共用。默认单行胶囊；编辑消息，或工作台聚焦、挂附件、长文本时展开为多行指挥台。此组件是受控组件：父组件持有 text/pending/sending/recording 等状态，这里只渲染 + 把事件转回父组件。命令弹层的筛选与高亮由本组件维护。
import type React from 'react'
import {
  type ClipboardEvent,
  type Dispatch,
  type PointerEvent,
  type SetStateAction,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react'

import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { FileText, FolderOpen, ImageIcon, Mic, Plus, Send, Slash, SquareFilled, Video, X } from '@/shared/lib/icons'
import {
  $slashCommandMeta,
  fetchSlashCommandMeta,
  fuzzyFilterCommands,
  type SlashCommandMeta
} from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import type { ChatAttachment } from '@protocol'

import { attachVideoFile, pickFile, pickFolder, pickImage, pickVideo } from './chat-attach-picker'
import type { ConversationVariant } from './chat-dock-message-bubble'
import { EditRetainedAttachments, PendingAttachmentView } from './chat-pending-attachment'
import { type PendingAttachment } from './chat-store'
import { useConversationView } from './conversation-view'
import { SlashCommandPopover } from './slash-command-popover'

export interface ChatSubmitState {
  editAttachments?: ChatAttachment[]
  editMessageId?: number
  gatewayState: ConnectionState
  isGenerating: boolean
  isReadOnlySession: boolean
  pending: PendingAttachment | null
  recording: boolean
  sending: boolean
  text: string
}

export interface ConversationInputProps {
  attachMenuOpen: boolean
  externalPaths: string[]
  onAttachMenuToggle: Dispatch<SetStateAction<boolean>>
  onCancelEdit: () => void
  onDrop: (e: React.DragEvent) => void
  onPaste: (e: ClipboardEvent) => void | Promise<void>
  onRecordingPointerCancel: (e: PointerEvent<HTMLButtonElement>) => void
  onRecordingPointerDown: (e: PointerEvent<HTMLButtonElement>) => void
  onRecordingPointerUp: (e: PointerEvent<HTMLButtonElement>) => void
  onSend: () => void
  onSetPending: Dispatch<SetStateAction<PendingAttachment | null>>
  onSetText: (next: string) => void
  onStop: () => void
  submit: ChatSubmitState
  variant?: ConversationVariant
}

// 工作台指挥台的展开阈值：超过这个长度、存在附件或聚焦时，长成 2–4 行 textarea。生活空间始终走单行胶囊。
const COMMAND_LINE_THRESHOLD = 80

const ATTACH_MENU = [
  { Icon: FileText, iconClass: 'text-accent', labelKey: 'addFile', pick: pickFile },
  { Icon: FolderOpen, iconClass: 'text-amber-400', labelKey: 'addFolder', pick: pickFolder },
  { Icon: ImageIcon, iconClass: 'text-emerald-400', labelKey: 'addImage', pick: pickImage },
  { Icon: Video, iconClass: 'text-rose-400', labelKey: 'addVideo', pick: pickVideo }
] as const

export function ConversationInput(props: ConversationInputProps): React.JSX.Element {
  const { controller, eligible, scoped } = useConversationView()
  const waitingForSession = scoped && controller.$chatSessionId.get() === null
  const { schedulePendingFlush } = controller

  const {
    attachMenuOpen,
    externalPaths,
    onAttachMenuToggle,
    onCancelEdit,
    onDrop,
    onPaste,
    onRecordingPointerCancel,
    onRecordingPointerDown,
    onRecordingPointerUp,
    onSend,
    onSetPending,
    onSetText,
    onStop,
    submit,
    variant = 'living'
  } = props

  const {
    editAttachments,
    editMessageId,
    gatewayState,
    isGenerating,
    isReadOnlySession,
    pending,
    recording,
    sending,
    text
  } = submit

  const isEditing = editMessageId !== undefined

  const [slashDismissed, setSlashDismissed] = useState(false)
  const [highlightIdx, setHighlightIdx] = useState(0)
  const [focused, setFocused] = useState(false)
  const [slashPaletteForced, setSlashPaletteForced] = useState(false)

  const dict = useStrings()
  const slashMeta = useStore($slashCommandMeta)

  // 工作台只在「要打字了」时升格成指挥台——空闲保持胶囊形态。
  const expanded =
    isEditing || (variant === 'workbench' && (focused || Boolean(pending) || text.length >= COMMAND_LINE_THRESHOLD))

  const editorRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null)

  // input 与 textarea 随展开切换，共用同一个 ref。
  const setEditorRef = useCallback((node: HTMLInputElement | HTMLTextAreaElement | null): void => {
    editorRef.current = node
  }, [])

  // 升格后把焦点同步进 textarea，避免升格瞬间丢失焦点。
  useEffect(() => {
    if (eligible && expanded && editorRef.current && document.activeElement !== editorRef.current) {
      editorRef.current.focus()
      const len = editorRef.current.value.length
      editorRef.current.setSelectionRange(len, len)
    }
  }, [editMessageId, expanded, eligible])

  // 仅前导 / 且尚未键入参数时，空 query 仍算命令模式，弹层展示全量。
  const slashContext = useMemo<{ active: boolean; query: string }>(() => {
    if (isEditing) {
      return { active: false, query: '' }
    }

    if (slashPaletteForced) {
      return { active: true, query: '' }
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
  }, [isEditing, slashPaletteForced, text])

  const items = slashContext.active ? fuzzyFilterCommands(slashContext.query, 8) : []
  const isOpen = eligible && !isEditing && slashContext.active && !slashDismissed && items.length > 0

  useEffect(() => {
    if (!slashContext.active || slashMeta.length > 0) {
      return
    }

    void fetchSlashCommandMeta()
  }, [slashContext.active, slashMeta.length])

  const handleSlashSelect = (cmd: SlashCommandMeta): void => {
    setSlashPaletteForced(false)
    onSetText(`/${cmd.name} `)
    editorRef.current?.focus()
    setSlashDismissed(true)
  }

  const showStop = !isEditing && isGenerating && !text.trim() && !pending && externalPaths.length === 0

  const sendDisabled =
    waitingForSession ||
    isReadOnlySession ||
    (isEditing && isGenerating) ||
    (!showStop &&
      (sending ||
        gatewayState !== 'open' ||
        (!text.trim() && !pending && externalPaths.length === 0) ||
        (pending?.type === 'video' && pending.status !== 'ready')))

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    schedulePendingFlush()
    setSlashPaletteForced(false)
    setSlashDismissed(false)
    setHighlightIdx(0)
    onSetText(e.target.value)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    if (!eligible) {
      return
    }

    if (isEditing && e.key === 'Escape' && !e.nativeEvent.isComposing) {
      e.preventDefault()
      e.stopPropagation()
      onCancelEdit()

      return
    }

    if (e.nativeEvent.isComposing || e.key.length === 1 || e.key === 'Backspace' || e.key === 'Delete') {
      schedulePendingFlush()
    }

    if (isOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setHighlightIdx((highlightIdx + 1) % items.length)

        return
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setHighlightIdx((highlightIdx - 1 + items.length) % items.length)

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
        setSlashDismissed(true)

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

      if (sendDisabled || (!isEditing && text.trim() === '/')) {
        return
      }

      onSend()
    }
  }

  const commonEditorProps = {
    disabled: waitingForSession || isReadOnlySession || (isEditing && sending),
    onBlur: () => setFocused(false),
    onChange: handleChange,
    onCompositionUpdate: () => schedulePendingFlush(),
    onFocus: () => setFocused(true),
    onKeyDown: handleKeyDown,
    onPaste,
    placeholder: waitingForSession
      ? dict.common.loading
      : variant === 'workbench'
        ? dict.chat.input.workbenchPlaceholder
        : dict.chat.inputPlaceholder,
    value: text
  }

  // 工作台指挥台：textarea；生活空间：单行 input。
  const editorElement = expanded ? (
    <textarea
      {...commonEditorProps}
      className="w-full flex-1 resize-none bg-transparent border-0 outline-none text-xs text-strong placeholder:text-faint px-1.5 py-1.5 min-h-[2.4em] max-h-[7.2em] leading-snug"
      ref={setEditorRef}
      rows={Math.min(4, Math.max(2, (text.match(/\n/g)?.length ?? 0) + 1))}
    />
  ) : (
    <input
      {...commonEditorProps}
      className="h-full flex-1 bg-transparent border-0 outline-none text-xs px-1.5 text-strong placeholder:text-faint"
      ref={setEditorRef}
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
      onDragOver={e => e.preventDefault()}
      onDrop={e => {
        // 阻止冒泡：whisper-overlay 等父容器同时挂着 onDrop 接收整层浮层的拖入，这里消费后不必再交给外层，否则文件路径会重复入附件。
        e.stopPropagation()
        onDrop(e)
      }}
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

      {editAttachments?.length ? (
        <div className="px-1">
          <EditRetainedAttachments attachments={editAttachments} />
        </div>
      ) : null}

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
            items={items}
            onHighlight={setHighlightIdx}
            onSelect={handleSlashSelect}
          />
        )}

        {editorElement}
      </div>

      <div className="flex items-center justify-between gap-1.5">
        {isEditing && (
          <span className="min-w-0 text-[11px] text-muted" title={dict.chat.edit.hint}>
            {dict.chat.edit.label}
          </span>
        )}
        <div className={cn('flex items-center gap-1.5', isEditing && 'hidden')}>
          <div className="relative shrink-0">
            <button
              aria-label={dict.chat.input.addAttachment}
              className={cn(
                'inline-flex size-7 items-center justify-center rounded-full border border-line-hairline bg-fill-faint text-muted transition hover:border-line-standard hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40',
                attachMenuOpen && 'border-line-strong bg-fill-hover text-strong'
              )}
              disabled={isReadOnlySession}
              onClick={() => onAttachMenuToggle(!attachMenuOpen)}
              title={dict.chat.input.addAttachment}
              type="button"
            >
              <Plus className="size-4" />
            </button>

            {attachMenuOpen && !isEditing && (
              <div className="absolute bottom-full mb-2 left-0 z-50 flex w-36 flex-col gap-0.5 rounded-xl border border-line-standard bg-surface-card p-1 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150">
                {ATTACH_MENU.map(({ Icon, iconClass, labelKey, pick }) => (
                  <button
                    className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                    key={labelKey}
                    onClick={() => void pick(onSetPending, controller)}
                    type="button"
                  >
                    <Icon className={cn('size-3.5', iconClass)} />
                    <span>{dict.chat.input[labelKey]}</span>
                  </button>
                ))}
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
                onAttachMenuToggle(false)

                if (isOpen) {
                  setSlashPaletteForced(false)
                  setSlashDismissed(true)

                  return
                }

                setSlashPaletteForced(true)
                setSlashDismissed(false)

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
          {isEditing && (
            <button
              aria-label={dict.chat.edit.cancel}
              className="inline-flex size-7 items-center justify-center rounded-full text-muted transition hover:bg-fill-hover hover:text-strong disabled:opacity-40"
              disabled={sending}
              onClick={onCancelEdit}
              title={dict.chat.edit.cancel}
              type="button"
            >
              <X className="size-4" />
            </button>
          )}
          <button
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-full border border-line-hairline bg-fill-faint text-muted transition hover:border-line-standard hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40',
              isEditing && 'hidden',
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
            aria-label={
              showStop ? dict.chat.input.stopGenerating : isEditing ? dict.chat.edit.send : dict.chat.input.sendMessage
            }
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-xl transition disabled:pointer-events-none disabled:opacity-30',
              showStop
                ? 'bg-rose-500/90 hover:bg-rose-600 text-white shadow-xs'
                : 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_12px_rgba(37,99,235,0.6)]'
            )}
            disabled={sendDisabled}
            onClick={() => (showStop ? onStop() : onSend())}
            title={
              showStop
                ? dict.chat.input.stopGenerating
                : isEditing
                  ? dict.chat.edit.send
                  : dict.chat.input.sendMessageShortcut
            }
            type="button"
          >
            {showStop ? <SquareFilled className="size-3" /> : <Send className="size-3.5 -rotate-12" />}
          </button>
        </div>
      </div>
    </div>
  )
}
