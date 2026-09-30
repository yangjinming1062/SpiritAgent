import { sleep } from '@runtime'
import { atom, computed, map } from 'nanostores'

import {
  currentClearEpoch,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type {
  ChatAttachment,
  ChatMediaItem,
  CompanionBubble,
  CompanionMediaBubble,
  ReplyAudio,
  SessionMessage,
  SessionRuntimeInfo
} from '@/shared/types/spiritagent'

import { chatDisplayText } from './chat-display-text'
import { conversationVoiceSink } from './voice-link'
import { removeVoicePlayback } from './voice-playback'

export interface ChatMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
  /** 后端 Message.id——fork/undo 回传 source_message_id；hydrate 历史行与活路径绑定后都有值。 */
  backendMessageId?: number
  timestamp?: number
}

export interface ChatMessageBody {
  /** 完整用户输入；陪伴拆泡与附件展示文案不能用作编辑原文。 */
  editableText?: string
  streamingText?: string
  replyType?: CompanionBubble['type']
  replyMedia?: CompanionMediaBubble
  replyIndex?: number
  replyAudio?: ReplyAudio | null
  text: string
  reasoning?: string
  streaming?: boolean
  queued?: boolean
  toolName?: string | null
  tools?: string[]
  error?: string
  cancelled?: boolean
  attachments?: ChatAttachment[]
  media?: ChatMediaItem[]
}

// 媒体终态可能早于完成帧；保留更新，避免迟到的等待快照把已就绪卡片覆盖回去。
const mediaUpdates = new Map<string, CompanionMediaBubble>()
const mediaUpdateKey = (messageId: number, mediaId: string): string => `${messageId}:${mediaId}`

function companionBubbleBody(bubble: CompanionBubble, messageId?: number): Partial<ChatMessageBody> {
  if (bubble.type === 'image' || bubble.type === 'video') {
    const current = messageId === undefined ? undefined : mediaUpdates.get(mediaUpdateKey(messageId, bubble.media_id))
    const media = current ?? bubble

    if (messageId !== undefined && media.status !== 'pending') {
      mediaUpdates.set(mediaUpdateKey(messageId, media.media_id), media)
    }

    return {
      text: '',
      replyType: media.type,
      replyMedia: media,
      media: media.status === 'ready' && media.url ? [{ type: media.type, url: media.url }] : undefined
    }
  }

  return 'text' in bubble
    ? { text: bubble.text, replyType: bubble.type, replyAudio: bubble.type === 'voice' ? bubble.audio : undefined }
    : {}
}

const DEFAULT_CONTEXT_LIMIT = 1_000_000
const CHAT_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.chatSessionId')
const COMPANION_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.companionSessionId')
const FLUSH_DEBOUNCE_MS = 4000

let idCounter = 0
const nextId = (): string => `m${++idCounter}`

let bubbleTimer: ReturnType<typeof setTimeout> | null = null
let bubbleGeneration = 0
let flushTimer: ReturnType<typeof setTimeout> | null = null
// 最近一次已提交批对应的用户气泡 id（工作台合并后只剩首条）；message.persisted 只按本集合绑定，失败回合孤儿气泡不会被下一轮错绑。
let submittedBubbleIds: Set<string> = new Set()
let historyEditRevision = 0

export const $chatMessageList = atom<ChatMessageListItem[]>([])
export const $chatMessageBodies = map<Record<string, ChatMessageBody>>({})
export const $lastAssistantStreaming = atom<boolean>(false)
export const $chatStreamingTick = atom<number>(0)
export const $chatSessionId = atom<string | null>(storedString(CHAT_SESSION_ID_KEY))
// 放在 chat-store：本模块要读它，而 session-list-store 已依赖 chat-store，反向导入会成环。
export const $companionSessionId = atom<string | null>(storedString(COMPANION_SESSION_ID_KEY))

export function setCompanionSessionId(id: string): void {
  $companionSessionId.set(id)
  persistString(COMPANION_SESSION_ID_KEY, id)
}

// IM 守卫与语音入口的权威 kind 源，由 hydrate 注入服务端 info.kind（special / standard / im）。
export type ChatSessionKind = 'im' | 'special' | 'standard'

function normalizeChatSessionKind(raw: unknown): ChatSessionKind {
  return raw === 'im' || raw === 'special' || raw === 'standard' ? raw : 'standard'
}

export const $chatSessionKind = atom<ChatSessionKind>('standard')

interface PendingPromptItem {
  text: string
  attachments?: ChatAttachment[]
  messageId?: string
}

export const $pendingPromptBatch = atom<PendingPromptItem[]>([])

export const $chatTurnInFlight = atom<boolean>(false)

export interface ChatEditDraft {
  sessionId: string
  sourceMessageId: number
  text: string
}

export const $chatEditDraft = atom<ChatEditDraft | null>(null)

export const $lastEditableUserMessage = computed(
  [$chatMessageList, $chatSessionKind, $chatTurnInFlight, $pendingPromptBatch],
  (list, kind, inFlight, pending): ChatMessageListItem | null => {
    if (kind === 'im' || inFlight || pending.length > 0) {
      return null
    }

    const last = list.findLast(item => item.role === 'user')

    return last?.backendMessageId && !last.subtype ? last : null
  }
)

export function startEditingMessage(messageId: string): void {
  const message = $lastEditableUserMessage.get()
  const sessionId = $chatSessionId.get()
  const body = $chatMessageBodies.get()[messageId]

  if (!sessionId || message?.id !== messageId || !message.backendMessageId || !body) {
    return
  }

  $chatEditDraft.set({
    sessionId,
    sourceMessageId: message.backendMessageId,
    text: (body.editableText ?? body.text)
      .split('\n')
      .filter(line => !/^@(file|folder):/i.test(line.trim()))
      .join('\n')
      .trim()
  })
}

// 当后端在 in-flight 回合期间发出 bubble.break 时置位，防止 message.complete 的全文/推理覆盖末尾气泡。
export const $turnHadBubbleBreak = atom<boolean>(false)

interface ChatUndoDraft {
  session_id: string
  text: string
  content_type?: string
  media_json?: string | null
}

// 撤回落草稿总线：undo 成功后由 session-list-store 写入；多窗口订阅需按 session_id 过滤，避免 A 撤回落到 B 的输入框。
export const $chatDraftFromUndo = atom<ChatUndoDraft | null>(null)

interface SessionSettings {
  temperature?: number
  context_compression_threshold?: number
  enable_context_compression?: boolean
  reasoning_effort?: string
}

export const $sessionSettings = atom<SessionSettings>({})

// 服务端 settings 是开放字典，只保留客户端消费且类型相符的键。
function toSessionSettings(raw: Record<string, unknown> = {}): SessionSettings {
  const settings: SessionSettings = {}

  if (typeof raw.temperature === 'number') {
    settings.temperature = raw.temperature
  }

  if (typeof raw.context_compression_threshold === 'number') {
    settings.context_compression_threshold = raw.context_compression_threshold
  }

  if (typeof raw.enable_context_compression === 'boolean') {
    settings.enable_context_compression = raw.enable_context_compression
  }

  if (typeof raw.reasoning_effort === 'string') {
    settings.reasoning_effort = raw.reasoning_effort
  }

  return settings
}

export function hydrateSessionSettings(info: SessionRuntimeInfo): void {
  $sessionSettings.set(toSessionSettings(info.settings))
}

export function updateSessionSetting<K extends keyof SessionSettings>(key: K, value: SessionSettings[K]): void {
  $sessionSettings.set({
    ...$sessionSettings.get(),
    [key]: value
  })
}

export interface SessionContextUsage {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  contextLimit: number
}

export const $sessionContextUsage = atom<SessionContextUsage>({
  promptTokens: 0,
  completionTokens: 0,
  totalTokens: 0,
  contextLimit: DEFAULT_CONTEXT_LIMIT
})

export function setSessionContextUsage(usage: Partial<SessionContextUsage>): void {
  const current = $sessionContextUsage.get()
  const promptTokens = usage.promptTokens ?? current.promptTokens
  const completionTokens = usage.completionTokens ?? current.completionTokens

  const totalTokens =
    usage.totalTokens ??
    (usage.promptTokens !== undefined || usage.completionTokens !== undefined
      ? promptTokens + completionTokens
      : current.totalTokens)

  const contextLimit = usage.contextLimit ?? current.contextLimit

  $sessionContextUsage.set({
    promptTokens,
    completionTokens,
    totalTokens,
    contextLimit
  })
}

export function resetSessionContextUsage(contextLimit?: number): void {
  $sessionContextUsage.set({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    contextLimit: contextLimit ?? DEFAULT_CONTEXT_LIMIT
  })
}

export type PendingAttachment =
  | { type: 'image'; value: string; fileName?: string }
  | {
      type: 'video'
      fileName: string
      path: string
      status: 'error' | 'ready' | 'uploading'
      url?: string
      error?: string
    }
  | {
      type: 'file'
      fileName: string
      path: string
    }
  | {
      type: 'folder'
      folderName: string
      path: string
    }

// 伙伴主动说出的瞬时消息，聊天面板收起时以气泡浮出，说完清空；sessionId 存在时点击切到该会话（媒体送达跳转用）。
interface ProactiveBubbleState {
  text: string
  sessionId?: string
}

export const $proactiveBubble = atom<ProactiveBubbleState | null>(null)

// 外部投喂（DESIGN「拖拽与直接交互」）：精灵拖入或经主进程信箱转交的文件路径，对话输入订阅后并入待发附件。
interface PendingExternalAttachment {
  paths: string[]
  nonce: number
}

let externalNonce = 0

export const $pendingExternalAttachment = atom<PendingExternalAttachment | null>(null)

export function pushExternalAttachment(paths: string[]): void {
  $pendingExternalAttachment.set({ paths, nonce: ++externalNonce })
}

export function clearExternalAttachment(): void {
  $pendingExternalAttachment.set(null)
}

export function setChatSession(id: string | null): void {
  conversationVoiceSink().cancel()
  clearPendingPrompts()
  cancelPendingFlush()
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)

  if ($chatSessionId.get() !== id) {
    mediaUpdates.clear()
    $chatEditDraft.set(null)
    $sessionSettings.set({})
    resetSessionContextUsage()
  }

  $chatSessionId.set(id)
  persistString(CHAT_SESSION_ID_KEY, id)
  // setChatSession 是无 info 的重置路径；后续 hydrate 会以服务端权威 kind 覆盖此值。
  $chatSessionKind.set('standard')
}

// 用从后端加载的会话替换面板的聊天记录；其他窗口可能正在连发或等待提交确认，历史修订不能删掉未落库的输入。
export function hydrateEditedChatMessages(messages: SessionMessage[]): void {
  forgetDeletedVoiceMessages(messages)
  historyEditRevision++
  const pendingIds = new Set($pendingPromptBatch.get().map(item => item.messageId))

  const pendingRows = $chatMessageList
    .get()
    .filter(item => pendingIds.has(item.id) || (submittedBubbleIds.has(item.id) && item.backendMessageId === undefined))

  const previousBodies = $chatMessageBodies.get()

  $chatTurnInFlight.set(true)
  hydrateChatMessages(messages)
  $chatEditDraft.set(null)

  for (const item of pendingRows) {
    $chatMessageBodies.setKey(item.id, previousBodies[item.id])
  }

  $chatMessageList.set([...$chatMessageList.get(), ...pendingRows])
}

export function hydrateChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo): void {
  conversationVoiceSink().cancel()
  const items: ChatMessageListItem[] = []
  const bodies: Record<string, ChatMessageBody> = {}

  let totalChars = 0
  const pendingReasoning: string[] = []

  const takeReasoning = (current?: string): string | undefined => {
    const parts = [...pendingReasoning, current].filter((part): part is string => Boolean(part?.trim()))
    pendingReasoning.length = 0

    return parts.length ? parts.join('\n\n') : undefined
  }

  const flushPendingReasoning = (timestamp?: number): void => {
    const reasoning = takeReasoning()

    if (!reasoning) {
      return
    }

    const id = nextId()
    items.push({ id, role: 'assistant', timestamp })
    bodies[id] = { text: '', reasoning, streaming: false, toolName: null }
  }

  for (const m of messages) {
    if (m.role === 'tool') {
      continue
    }

    const companionBubbles = m.role === 'assistant' && m.content_type === 'companion_reply' ? m.bubbles : undefined

    const textContent =
      m.content_type === 'companion_reply'
        ? ''
        : m.role === 'assistant'
          ? chatDisplayText(extractText(m))
          : extractText(m)

    const reasoningContent = typeof m.reasoning === 'string' ? m.reasoning : ''

    // 无正文无媒体的助手行（工具中间帧）不单独占气泡，推理并到下一可见助手行。
    if (m.role === 'assistant' && !companionBubbles?.length && !textContent.trim() && !m.media?.length) {
      if (reasoningContent.trim()) {
        pendingReasoning.push(reasoningContent)
      }

      continue
    }

    if (m.role === 'user') {
      flushPendingReasoning(m.timestamp)
    }

    totalChars +=
      m.content_type === 'companion_reply' && typeof m.content === 'string' ? m.content.length : textContent.length

    // 陪伴用户行按空行拆分（与实时呈现对齐），工作台整段阅读不拆。
    const canSplit = !m.subtype && m.role === 'user' && splitUserBubblesEnabled()
    // 后台视频送达的 system 行正文是给模型的任务记录，与实时送达一致只显示媒体卡。
    const hideText = m.role === 'system' && m.subtype === 'status_media'

    const segments = companionBubbles
      ? companionBubbles.map(bubble => ('text' in bubble ? bubble.text : ''))
      : canSplit
        ? textContent
            .split(/\r?\n(?:[ \t]*\r?\n)+/)
            .map(part => part.trim())
            .filter(Boolean)
        : [hideText ? '' : textContent]

    if (segments.length === 0) {
      segments.push('')
    }

    for (const [index, segment] of segments.entries()) {
      const id = nextId()

      items.push({
        id,
        role: m.role === 'user' ? 'user' : 'assistant',
        subtype: m.subtype,
        backendMessageId: typeof m.id === 'number' ? m.id : undefined,
        timestamp: m.timestamp
      })

      // 拆分后附件只挂首个气泡（附件伴随连发首条发出，每段都挂会重复渲染媒体卡）。
      bodies[id] = {
        text: segment,
        editableText: m.role === 'user' ? textContent : undefined,
        replyIndex: companionBubbles ? index : undefined,
        ...(companionBubbles?.[index] ? companionBubbleBody(companionBubbles[index], m.id) : {}),
        reasoning: m.role === 'assistant' && index === 0 ? takeReasoning(reasoningContent || undefined) : undefined,
        toolName: m.tool_name ?? null,
        tools: m.tool_name ? [m.tool_name] : undefined,
        streaming: false,
        queued: m.role === 'user' && m.queued,
        ...(m.role === 'user' && index === 0 ? omitUndefined(extractUserAttachments(m)) : {}),
        ...(!companionBubbles && index === segments.length - 1 && m.media?.length ? { media: m.media } : {})
      }
    }
  }

  flushPendingReasoning()

  $chatMessageBodies.set(bodies)
  $chatMessageList.set(items)
  $lastAssistantStreaming.set(false)

  if (info) {
    hydrateSessionSettings(info)
    // 缺字段/未知值回落 standard 以免 IM 守卫误判；无 info 的本会话内操作（撤回/清空/压缩重水合）沿用当前 kind，重置会解除 IM 只读。
    $chatSessionKind.set(normalizeChatSessionKind(info.kind))
  }

  // 估算 Token 占用（~3 字符/Token）；先清零分项避免切换会话残留，无 info 的本会话重水合沿用当前上下文上限。
  const approxTokens = Math.round(totalChars / 3)
  const contextLimit = info ? info.context_window || DEFAULT_CONTEXT_LIMIT : $sessionContextUsage.get().contextLimit
  resetSessionContextUsage(contextLimit)
  setSessionContextUsage({ totalTokens: approxTokens })
}

function extractText(m: SessionMessage): string {
  if (typeof m.content !== 'string') {
    return ''
  }

  if (m.content_type !== 'multimodal_v1') {
    return m.content
  }

  let parsed: unknown

  try {
    parsed = JSON.parse(m.content)
  } catch {
    return m.content
  }

  if (!Array.isArray(parsed)) {
    return m.content.trim()
  }

  return parsed
    .filter(
      (p): p is { type: 'input_text'; text: string } =>
        typeof p === 'object' && p !== null && p.type === 'input_text' && typeof p.text === 'string'
    )
    .map(p => p.text)
    .join('\n')
    .trim()
}

// 多模态用户行的 input_image/input_video parts 还原为类型化附件供气泡渲染；清理后的视频只剩文本 part，落不进附件列表。
function extractUserAttachments(m: SessionMessage): ChatAttachment[] | undefined {
  if (m.content_type !== 'multimodal_v1' || typeof m.content !== 'string') {
    return undefined
  }

  let parsed: unknown

  try {
    parsed = JSON.parse(m.content)
  } catch {
    return undefined
  }

  if (!Array.isArray(parsed)) {
    return undefined
  }

  const attachments = parsed
    .filter(
      (p): p is { type?: string; image_url?: unknown; video_url?: unknown } =>
        typeof p === 'object' &&
        p !== null &&
        ((p as { type?: unknown }).type === 'input_image' || (p as { type?: unknown }).type === 'input_video')
    )
    .map(p =>
      p.type === 'input_video'
        ? { type: 'video' as const, url: typeof p.video_url === 'string' ? p.video_url : '' }
        : { type: 'image' as const, url: typeof p.image_url === 'string' ? p.image_url : '' }
    )
    .filter(a => a.url.length > 0)

  return attachments.length ? attachments : undefined
}

function omitUndefined(attachments: ChatAttachment[] | undefined): { attachments?: ChatAttachment[] } {
  return attachments ? { attachments } : {}
}

export function setProactiveBubble(state: ProactiveBubbleState | null, lingerMs?: number): void {
  if (bubbleTimer) {
    clearTimeout(bubbleTimer)
    bubbleTimer = null
  }

  $proactiveBubble.set(state)

  if (state && lingerMs != null && lingerMs > 0) {
    const gen = ++bubbleGeneration

    bubbleTimer = setTimeout(() => {
      // 连续主动消息/媒体提示时，只清理自己这一代的气泡。
      if (gen === bubbleGeneration) {
        $proactiveBubble.set(null)
        bubbleTimer = null
      }
    }, lingerMs)
  }
}

export function showMediaHint(text: string, sessionId?: string): void {
  setProactiveBubble(sessionId ? { text, sessionId } : { text }, 8000)
}

export function pushProactiveMessage(text: string, media?: ChatMediaItem[], messageId?: number): void {
  if (messageId && $chatMessageList.get().some(item => item.backendMessageId === messageId)) {
    return
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, { text: chatDisplayText(text), media, streaming: false, toolName: null })
  $chatMessageList.set([
    ...$chatMessageList.get(),
    {
      id,
      role: 'assistant',
      subtype: media?.length ? 'status_media' : 'status_proactive',
      backendMessageId: messageId,
      timestamp: Date.now()
    }
  ])
}

// 后台视频完成的实时送达行，只带媒体；历史水合的同类 system 行同样不显示正文。
export function pushMediaMessage(media: ChatMediaItem[]): string {
  const id = nextId()
  $chatMessageBodies.setKey(id, { text: '', media, streaming: false, toolName: null })
  $chatMessageList.set([
    ...$chatMessageList.get(),
    { id, role: 'assistant', subtype: 'status_media', timestamp: Date.now() }
  ])

  return id
}

export function pushUserMessage(text: string, attachments?: ChatAttachment[]): string {
  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text,
    attachments: attachments?.length ? attachments : undefined,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'user', timestamp: Date.now() }])

  return id
}

function isPositiveInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

// 陪伴会话连发用户消息保留独立气泡（与 bubble.break 助手拆分对称），工作台整段阅读不拆，避免误拆粘贴的多段内容。
function splitUserBubblesEnabled(): boolean {
  const id = $chatSessionId.get()

  return id !== null && id === $companionSessionId.get()
}

export function bindTrailingUserMessageIds(ids: number[]): void {
  // 只绑本次提交的气泡，失败回合孤儿气泡不被下一轮错绑（错绑会让撤回截断别人的消息）；连发拆泡时超出 id 数的气泡挂最后一个 id，与 hydrate 同行同 id 语义一致。
  const validIds = ids.filter(isPositiveInt)

  if (validIds.length === 0) {
    return
  }

  const list = $chatMessageList.get()

  // 编辑事件已经水合了修订行；随后重放的落库通知不属于本窗口待确认的提交。
  if (validIds.every(id => list.some(item => item.backendMessageId === id))) {
    return
  }

  const unboundIndexes: number[] = []

  for (let i = list.length - 1; i >= 0; i--) {
    const item = list[i]

    if (item?.role === 'user' && item.backendMessageId === undefined && submittedBubbleIds.has(item.id)) {
      unboundIndexes.push(i)
    }
  }

  if (unboundIndexes.length === 0) {
    return
  }

  unboundIndexes.reverse()
  const next = list.slice()
  let changed = false

  for (const [index, idx] of unboundIndexes.entries()) {
    const messageId = validIds[index] ?? validIds[validIds.length - 1]
    next[idx] = { ...next[idx], backendMessageId: messageId }
    changed = true
  }

  if (changed) {
    $chatMessageList.set(next)
  }
}

export function bindTrailingAssistantMessageId(messageId: number): void {
  // bubble.break 会拆出多段助手气泡，但 DB 只有一行；同一 id 挂到上次用户之后所有未绑定的普通助手行。
  if (!isPositiveInt(messageId)) {
    return
  }

  const list = $chatMessageList.get()
  let lastUserIndex = -1

  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i]?.role === 'user') {
      lastUserIndex = i

      break
    }
  }

  let changed = false
  const next = list.slice()

  for (let i = lastUserIndex + 1; i < next.length; i++) {
    const item = next[i]

    // 压缩卡片等 subtype 行不是终端助手气泡，不能挂上同一条 message_id。
    if (item.role === 'assistant' && item.backendMessageId === undefined && !item.subtype) {
      next[i] = { ...item, backendMessageId: messageId }
      changed = true
    }
  }

  if (changed) {
    $chatMessageList.set(next)
  }
}

/** 追加一行本地状态行（如 `status_command_result`、`compress_summary`），渲染层按 subtype 显示为居中 pill 或摘要卡片。 */
export function pushStatusPill(subtype: string, text: string): void {
  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype, timestamp: Date.now() }])
}

export function pushPendingPrompt(item: PendingPromptItem): void {
  const last = $chatMessageList.get().at(-1)
  $pendingPromptBatch.set([
    ...$pendingPromptBatch.get(),
    { ...item, messageId: last?.role === 'user' ? last.id : undefined }
  ])
}

function drainPendingPrompts(): PendingPromptItem[] {
  const items = $pendingPromptBatch.get()
  $pendingPromptBatch.set([])

  return items
}

export function clearPendingPrompts(): void {
  $pendingPromptBatch.set([])
}

registerStorageClearHandler(() => {
  conversationVoiceSink().cancel()
  $chatSessionId.set(null)
  $companionSessionId.set(null)
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $lastAssistantStreaming.set(false)
  $chatStreamingTick.set(0)
  $chatSessionKind.set('standard')
  $sessionSettings.set({})
  $sessionContextUsage.set({
    completionTokens: 0,
    contextLimit: DEFAULT_CONTEXT_LIMIT,
    promptTokens: 0,
    totalTokens: 0
  })
  $proactiveBubble.set(null)
  $pendingExternalAttachment.set(null)
  $pendingPromptBatch.set([])
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
  $chatDraftFromUndo.set(null)
  $chatEditDraft.set(null)

  if (flushTimer) {
    clearTimeout(flushTimer)
    flushTimer = null
  }

  if (bubbleTimer) {
    clearTimeout(bubbleTimer)
    bubbleTimer = null
  }
})

export function setTurnHadBubbleBreak(v: boolean): void {
  $turnHadBubbleBreak.set(v)
}

export function schedulePendingFlush(): void {
  if ($pendingPromptBatch.get().length === 0) {
    return
  }

  if (flushTimer) {
    clearTimeout(flushTimer)
  }

  flushTimer = setTimeout(() => {
    flushTimer = null
    submitPendingBatch()
  }, FLUSH_DEBOUNCE_MS)
}

export function cancelPendingFlush(): void {
  if (flushTimer) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
}

export function submitPendingBatch(): void {
  if ($chatTurnInFlight.get() || flushTimer !== null) {
    return
  }

  const sessionId = $chatSessionId.get()
  const gateway = $gateway.get()

  if (!sessionId || !gateway || gateway.connectionState !== 'open') {
    return
  }

  const pendingBatch = drainPendingPrompts()

  if (pendingBatch.length === 0) {
    return
  }

  $chatTurnInFlight.set(true)

  const pendingIds = new Set(pendingBatch.map(p => p.messageId).filter((id): id is string => Boolean(id)))
  const list = $chatMessageList.get()
  const pendingRows = list.filter(item => pendingIds.has(item.id))
  const first = pendingRows[0]

  // 工作台连发合回首条再提交；陪伴会话每条连发保留独立气泡（DB 仍合并为一行）。
  if (first && !splitUserBubblesEnabled()) {
    const bodies = $chatMessageBodies.get()
    const displayAttachments = pendingRows.flatMap(item => bodies[item.id]?.attachments ?? [])
    $chatMessageBodies.setKey(first.id, {
      ...bodies[first.id],
      text: pendingRows
        .map(item => bodies[item.id]?.text ?? '')
        .filter(Boolean)
        .join('\n\n'),
      attachments: displayAttachments.length ? displayAttachments : undefined
    })
    $chatMessageList.set([...list.filter(item => !pendingIds.has(item.id)), first])

    for (const item of pendingRows.slice(1)) {
      $chatMessageBodies.setKey(item.id, undefined)
    }
  }

  // 记录本批对应的存活气泡 id（合并路径只剩首条），persisted 据此精确绑定。
  submittedBubbleIds = new Set(pendingRows.map(item => item.id))

  const attachments = pendingBatch.flatMap(p => p.attachments ?? [])
  const promptText = pendingBatch.map(p => p.text).join('\n\n')

  for (const id of submittedBubbleIds) {
    const body = $chatMessageBodies.get()[id]

    if (body) {
      $chatMessageBodies.setKey(id, { ...body, editableText: promptText })
    }
  }

  const batchPayload = {
    session_id: sessionId,
    response_preference: presentationPorts().getResponsePreference(),
    batch: [
      {
        text: promptText,
        ...(attachments.length ? { attachments: attachments.map(a => ({ file_url: a.url, type: a.type })) } : {})
      }
    ]
  }

  const submittedRevision = historyEditRevision
  const epoch = currentClearEpoch()

  // 本批未送达：不当作已发送。会话已切走时不写进新会话的列表与回合状态，改用通知。
  const failSubmit = (err?: unknown): void => {
    if (epoch !== currentClearEpoch()) {
      return
    }

    const sendFailed = getStrings().chat.sendFailed

    if ($chatSessionId.get() !== sessionId) {
      notifyError(err, sendFailed)

      return
    }

    markAssistantTerminal({ error: err instanceof Error ? err.message : sendFailed })
    // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
    presentationPorts().setSpriteState('idle', { force: true })
    $chatTurnInFlight.set(false)
  }

  const submitWithRetry = async (attempt = 0): Promise<void> => {
    const g = $gateway.get()

    // 首次提交前已确认连接；只有退避重试期间断连会走到这里。
    if (!g || g.connectionState !== 'open') {
      failSubmit()

      return
    }

    try {
      presentationPorts().setSpriteState('thinking')
      await g.request('prompt.submit', batchPayload)
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err)

      if (
        errMsg.includes('in-flight') &&
        historyEditRevision !== submittedRevision &&
        $chatSessionId.get() === sessionId
      ) {
        // 另一个窗口的编辑先被接受；本批尚未落库，回到队列等该回合结束。
        $pendingPromptBatch.set([...pendingBatch, ...$pendingPromptBatch.get()])
        submitPendingBatch()

        return
      }

      if (errMsg.includes('in-flight') && attempt < 3) {
        await sleep(50 * Math.pow(2, attempt))

        return submitWithRetry(attempt + 1)
      }

      failSubmit(err)
    }
  }

  void submitWithRetry()
}

export function beginAssistantMessage(): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

  if (lastItem?.role === 'assistant' && lastBody?.streaming) {
    if (!lastBody.text.trim() && !lastBody.toolName && !lastBody.error && !lastBody.cancelled) {
      return
    }

    finalizeAssistantMessage()
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, { text: '', streaming: true, toolName: null })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', timestamp: Date.now() }])
  $lastAssistantStreaming.set(true)
}

function ensureAssistantMessage(): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

  if (lastItem && lastItem.role === 'assistant' && lastBody?.streaming) {
    return
  }

  beginAssistantMessage()
}

function patchLastAssistant(patch: (body: ChatMessageBody) => ChatMessageBody): void {
  ensureAssistantMessage()
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]

  if (!lastItem || lastItem.role !== 'assistant') {
    return
  }

  const body = $chatMessageBodies.get()[lastItem.id]

  if (!body) {
    return
  }

  $chatMessageBodies.setKey(lastItem.id, patch(body))
  $chatStreamingTick.set($chatStreamingTick.get() + 1)
}

export function appendAssistantDelta(text: string): void {
  // 仅更新流式 body 不动 list 引用；首个 delta 过滤前导空行，避免撑大气泡上方。
  patchLastAssistant(body => {
    const streamingText = (body.streamingText ?? body.text) + text

    return {
      ...body,
      streamingText,
      text: chatDisplayText(streamingText, true).trimStart()
    }
  })
}

export function appendAssistantReasoningDelta(text: string): void {
  patchLastAssistant(body => ({
    ...body,
    reasoning: !body.reasoning ? text.trimStart() : body.reasoning + text
  }))
}

export function setAssistantTool(name: string | null): void {
  ensureAssistantMessage()
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]

  if (!lastItem || lastItem.role !== 'assistant') {
    return
  }

  const body = $chatMessageBodies.get()[lastItem.id]

  if (!body) {
    return
  }

  const tools = name && name !== body.tools?.[body.tools.length - 1] ? [...(body.tools ?? []), name] : body.tools

  $chatMessageBodies.setKey(lastItem.id, { ...body, toolName: name, tools })
}

export function finalizeAssistantMessage(text?: string, media?: ChatMediaItem[], reasoning?: string): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]

  if (!lastItem || lastItem.role !== 'assistant') {
    return
  }

  const body = $chatMessageBodies.get()[lastItem.id]

  if (!body) {
    return
  }

  const rawStr = typeof text === 'string' ? text : (body.streamingText ?? body.text)
  const finalStr = chatDisplayText(rawStr).trim()
  const finalMedia = media ?? body.media

  const finalReasoning =
    (typeof reasoning === 'string' && reasoning.trim() ? reasoning : body.reasoning)?.trim() || undefined

  const isEmpty =
    !finalStr.trim() &&
    !finalReasoning?.trim() &&
    !body.toolName &&
    !body.error &&
    !body.cancelled &&
    !body.attachments?.length &&
    !finalMedia?.length

  if (isEmpty) {
    $chatMessageList.set(list.slice(0, -1))
    $chatMessageBodies.setKey(lastItem.id, undefined)
    $lastAssistantStreaming.set(false)

    return
  }

  $chatMessageBodies.setKey(lastItem.id, {
    ...body,
    text: finalStr,
    streamingText: undefined,
    reasoning: finalReasoning,
    media: finalMedia,
    streaming: false,
    toolName: null
  })
  $lastAssistantStreaming.set(false)
}

export function finalizeCompanionReply(
  bubbles: CompanionBubble[],
  messageId: number,
  reasoning?: string,
  proactive = false
): void {
  const list = $chatMessageList.get()

  if (list.some(item => item.backendMessageId === messageId)) {
    bubbles.forEach((bubble, index) => {
      if (bubble.type === 'image' || bubble.type === 'video') {
        updateMediaBubble(messageId, bubble.media_id, bubble)
      } else {
        updateVoiceBubble(messageId, index, bubble)
      }
    })

    return
  }

  const last = list.at(-1)
  const streaming = last?.role === 'assistant' && $chatMessageBodies.get()[last.id]?.streaming
  const placeholder = !proactive && streaming
  const next = streaming ? list.slice(0, -1) : [...list]
  const voiceIds: string[] = []

  if (placeholder && last) {
    $chatMessageBodies.setKey(last.id, undefined)
  }

  bubbles.forEach((bubble, index) => {
    const id = nextId()

    if (bubble.type === 'voice') {
      voiceIds.push(id)
    }

    next.push({
      id,
      role: 'assistant',
      backendMessageId: messageId,
      timestamp: Date.now(),
      ...(proactive ? { subtype: 'status_proactive' } : {})
    })
    $chatMessageBodies.setKey(id, {
      text: '',
      ...companionBubbleBody(bubble, messageId),
      replyIndex: index,
      streaming: false,
      toolName: null,
      ...(index === 0 ? { reasoning } : {})
    })
  })

  if (proactive && streaming && last) {
    next.push(last)
  }

  $chatMessageList.set(next)

  conversationVoiceSink().enqueue(voiceIds)

  if (!proactive) {
    $lastAssistantStreaming.set(false)
  }
}

export function updateMediaBubble(messageId: number, mediaId: string, bubble: CompanionMediaBubble): void {
  if (bubble.media_id !== mediaId) {
    return
  }

  const key = mediaUpdateKey(messageId, mediaId)
  const previous = mediaUpdates.get(key)

  if (previous && previous.status !== 'pending') {
    return
  }

  if (bubble.status !== 'pending') {
    mediaUpdates.set(key, bubble)
  }

  for (const item of $chatMessageList.get()) {
    const body = $chatMessageBodies.get()[item.id]

    if (item.backendMessageId !== messageId || body?.replyMedia?.media_id !== mediaId) {
      continue
    }

    if (body.replyMedia.status !== 'pending') {
      continue
    }

    $chatMessageBodies.setKey(item.id, { ...body, ...companionBubbleBody(bubble, messageId) })
  }
}

export function updateVoiceBubble(messageId: number, index: number, bubble: CompanionBubble): void {
  if (bubble.type !== 'voice') {
    return
  }

  for (const item of $chatMessageList.get()) {
    const body = $chatMessageBodies.get()[item.id]

    if (item.backendMessageId === messageId && body?.replyIndex === index && body.replyType === 'voice') {
      $chatMessageBodies.setKey(item.id, { ...body, replyAudio: bubble.audio ?? body.replyAudio })
    }
  }
}

// 仅历史编辑/撤回的完整结果调用；普通水合可能截断，不能据此删除播放记录。
export function forgetDeletedVoiceMessages(messages: SessionMessage[]): void {
  conversationVoiceSink().cancel()
  const sessionId = $chatSessionId.get()
  const remaining = new Set(messages.map(message => message.id))

  const removed = $chatMessageList
    .get()
    .flatMap(item => (item.backendMessageId && !remaining.has(item.backendMessageId) ? [item.backendMessageId] : []))

  if (sessionId) {
    removeVoicePlayback(sessionId, [...new Set(removed)])
  }
}

export function markAssistantTerminal({ error, cancelled }: { error?: string; cancelled?: boolean } = {}): void {
  conversationVoiceSink().cancel()

  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined
  const isStreaming = lastItem?.role === 'assistant' && lastBody?.streaming
  const terminal = { ...(error !== undefined && { error }), ...(cancelled && { cancelled: true }) }

  if (isStreaming && lastItem && lastBody) {
    $chatMessageBodies.setKey(lastItem.id, {
      ...lastBody,
      text: chatDisplayText(lastBody.streamingText ?? lastBody.text).trim(),
      streamingText: undefined,
      streaming: false,
      ...terminal
    })
    $lastAssistantStreaming.set(false)

    return
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text: '',
    ...terminal,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...list, { id, role: 'assistant', timestamp: Date.now() }])
  $lastAssistantStreaming.set(false)
}

// 重置消息列表与 bodies，不触碰 $chatSessionId 与 pending batch。
export function resetChatMessages(): void {
  mediaUpdates.clear()
  conversationVoiceSink().cancel()
  $chatEditDraft.set(null)
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $lastAssistantStreaming.set(false)
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
}
