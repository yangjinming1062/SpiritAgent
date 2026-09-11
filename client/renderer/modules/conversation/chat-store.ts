import type { SpeechStyle } from '@ipc/contracts'
import { sleep } from '@runtime'
import { atom, map } from 'nanostores'

import {
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $gateway } from '@/shared/store/gateway'
import { getStrings } from '@/shared/strings'
import type { ChatAttachment, ChatMediaItem, SessionMessage, SessionRuntimeInfo } from '@/shared/types/spiritagent'

import { speechText } from '../../../shared/speech-text'

import { chatDisplayText } from './chat-display-text'
import { conversationVoiceSink } from './voice-link'

export interface ChatMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
  /** 后端 Message.id——fork/undo 回传 source_message_id；hydrate 历史行与活路径绑定后都有值。 */
  backendMessageId?: number
  timestamp?: number
}

export interface ChatMessageBody {
  streamingText?: string
  speechStyle?: SpeechStyle
  text: string
  reasoning?: string
  streaming?: boolean
  toolName?: string | null
  tools?: string[]
  error?: string
  cancelled?: boolean
  attachments?: ChatAttachment[]
  media?: ChatMediaItem[]
  voiceStatus?: 'pending' | 'ready' | 'failed'
  voiceDuration?: number
}

const DEFAULT_CONTEXT_LIMIT = 1_000_000
const CHAT_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.chatSessionId')
const FLUSH_DEBOUNCE_MS = 4000

let idCounter = 0
const nextId = (): string => `m${++idCounter}`

let bubbleTimer: ReturnType<typeof setTimeout> | null = null
let bubbleGeneration = 0
let flushTimer: ReturnType<typeof setTimeout> | null = null

export const $chatMessageList = atom<ChatMessageListItem[]>([])
export const $chatMessageBodies = map<Record<string, ChatMessageBody>>({})
export const $lastAssistantStreaming = atom<boolean>(false)
export const $chatStreamingTick = atom<number>(0)
export const $chatSessionId = atom<string | null>(storedString(CHAT_SESSION_ID_KEY))
// 与 $chatSessionId 同层，避免 session-list-store 反向依赖 chat-store 造成循环初始化。
export const $companionSessionId = atom<string | null>(null)
// IM 守卫与语音入口的权威 kind 源：写值由 hydrate 把服务端 info.kind 注入。
// 与 PROTOCOL §2.1 Conversation.kind 对齐：special / standard / im。
export type ChatSessionKind = 'im' | 'special' | 'standard'

function normalizeChatSessionKind(raw: unknown): ChatSessionKind {
  return raw === 'im' || raw === 'special' || raw === 'standard' ? raw : 'standard'
}

export const $chatSessionKind = atom<ChatSessionKind>('standard')

// 当前会话独立参数配置（温度、压缩阈值、思考程度等）
interface SessionSettings {
  temperature?: number
  context_compression_threshold?: number
  enable_context_compression?: boolean
  reasoning_effort?: string
}

export const $sessionSettings = atom<SessionSettings>({})

export function hydrateSessionSettings(info: SessionRuntimeInfo): void {
  $sessionSettings.set((info.settings ?? {}) as SessionSettings)
}

export function updateSessionSetting<K extends keyof SessionSettings>(key: K, value: SessionSettings[K]): void {
  $sessionSettings.set({
    ...$sessionSettings.get(),
    [key]: value
  })
}

// 上下文使用量状态跟踪（已用 token、总容量、压缩阈值节点等）
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

// 待发送附件（图片 / 视频 / 文件 / 文件夹四种）
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

// 伙伴主动说出的瞬时消息，在聊天面板收起时以气泡形式浮出。说完后清空。
// sessionId 存在时点击气泡会切到该会话（媒体送达提示跳转用）。
interface ProactiveBubbleState {
  text: string
  sessionId?: string
}

export const $proactiveBubble = atom<ProactiveBubbleState | null>(null)

// 外部投喂（DESIGN §6.3「文件投喂」）——SpriteStage 拖拽文件到精灵本体时，
// 把文件路径推到此处。对话组件订阅并把首个图像文件塞入附件占位。
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
    $sessionSettings.set({})
    resetSessionContextUsage()
  }

  $chatSessionId.set(id)
  persistString(CHAT_SESSION_ID_KEY, id)
  // setChatSession 是无 info 的重置路径；后续 hydrate 会以服务端权威 kind 覆盖此值。
  $chatSessionKind.set('standard')
}

// 用从后端加载的会话替换面板的聊天记录。
export function hydrateChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo): void {
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
    // 过滤底层工具执行结果（role === 'tool'），避免将 raw JSON 结果作为气泡显示
    if (m.role === 'tool') {
      continue
    }

    const textContent = m.role === 'assistant' ? chatDisplayText(extractText(m)) : extractText(m)
    const reasoningContent = typeof m.reasoning === 'string' ? m.reasoning : ''

    // 无正文无媒体的助手行（工具中间帧）不单独占气泡；其推理过程并到下一可见助手行。
    if (m.role === 'assistant' && !textContent.trim() && !m.media?.length) {
      if (reasoningContent.trim()) {
        pendingReasoning.push(reasoningContent)
      }

      continue
    }

    if (m.role === 'user') {
      flushPendingReasoning(m.timestamp)
    }

    totalChars += textContent.length

    const segments =
      m.role === 'assistant' &&
      !m.subtype &&
      $chatSessionId.get() !== null &&
      $chatSessionId.get() === $companionSessionId.get()
        ? textContent
            .split(/\r?\n(?:[ \t]*\r?\n)+/)
            .map(part => part.trim())
            .filter(Boolean)
        : [textContent]

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

      const cachedVoiceDuration =
        m.role === 'assistant' && segment ? conversationVoiceSink().cachedDuration(segment, m.speech_style) : undefined

      bodies[id] = {
        text: segment,
        speechStyle: m.speech_style,
        reasoning: m.role === 'assistant' && index === 0 ? takeReasoning(reasoningContent || undefined) : undefined,
        toolName: m.tool_name ?? null,
        tools: m.tool_name ? [m.tool_name] : undefined,
        streaming: false,
        voiceDuration: cachedVoiceDuration,
        voiceStatus: cachedVoiceDuration ? 'ready' : undefined,
        ...(m.role === 'user' ? omitUndefined(extractUserAttachments(m)) : {}),
        ...(index === segments.length - 1 && m.media?.length ? { media: m.media } : {})
      }
    }
  }

  flushPendingReasoning()

  $chatMessageBodies.set(bodies)
  $chatMessageList.set(items)
  $lastAssistantStreaming.set(false)

  if (info) {
    hydrateSessionSettings(info)
  }

  // 缺字段/未知值回落 standard，与 setChatSession 兜底一致——避免 IM 守卫在 hydrate 完成前的瞬间误判。
  $chatSessionKind.set(normalizeChatSessionKind(info?.kind))

  // 估算 Token 占用（无精确 usage 时的兜底估算：~3 字符/Token）
  // 先清零分项，避免切换会话后残留上一会话的 prompt/completion。
  const approxTokens = Math.round(totalChars / 3)
  resetSessionContextUsage(info?.context_window || DEFAULT_CONTEXT_LIMIT)
  setSessionContextUsage({
    totalTokens: approxTokens,
    contextLimit: info?.context_window || DEFAULT_CONTEXT_LIMIT
  })
}

function extractText(m: SessionMessage): string {
  // ``SessionMessage.content`` 类型未知——部分用户消息以 JSON parts 数组
  // 到达（含 image_url 等多模态部分）；只渲染用户可见文本，避免漏出
  // ``[{"type": "input_image", ...}]`` 之类的非文本部分。
  if (typeof m.content !== 'string') {
    return ''
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
    .filter((p): p is { type?: string; text?: string } => typeof p === 'object' && p !== null)
    .filter(p => p.type === 'input_text' && typeof p.text === 'string')
    .map(p => p.text as string)
    .join('\n')
    .trim()
}

// 多模态用户行里的 input_image/input_video parts 还原为类型化附件列表，
// 供气泡渲染媒体卡；纯文本行与无附件行返回 undefined。被清理的视频行只剩
// [视频已清理] 文本 part，天然落不进附件列表。
function extractUserAttachments(m: SessionMessage): ChatAttachment[] | undefined {
  if (typeof m.content !== 'string') {
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

// 后台视频完成等异步送达的媒体行；与历史水合的 status_media 行同形状。
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

export function bindTrailingUserMessageIds(ids: number[]): void {
  // 活路径 push 时没有后端 id；按末尾未绑定用户行从旧到新填，避免覆盖 hydrate 已有的 id。
  const validIds = ids.filter(isPositiveInt)

  if (validIds.length === 0) {
    return
  }

  const list = $chatMessageList.get()
  const unboundIndexes: number[] = []

  for (let i = list.length - 1; i >= 0 && unboundIndexes.length < validIds.length; i--) {
    if (list[i]?.role === 'user' && list[i].backendMessageId === undefined) {
      unboundIndexes.push(i)
    }
  }

  if (unboundIndexes.length === 0) {
    return
  }

  unboundIndexes.reverse()
  const next = list.slice()
  const count = Math.min(unboundIndexes.length, validIds.length)
  const idOffset = validIds.length - count

  for (let i = 0; i < count; i++) {
    const idx = unboundIndexes[i]
    next[idx] = { ...next[idx], backendMessageId: validIds[idOffset + i] }
  }

  $chatMessageList.set(next)
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

/**
 * 把一行 status pill（如 `status_cleared` / `compress_summary` / 自定义 command_result）插入消息列表。
 *
 * Pill 在渲染层走 `status_*` / `compress_summary` 通用路径（与每日摘要、压缩摘要同形态）。
 * 文本为空时返回的 id 是新插入消息的本地 id（便于滚动定位等场景）。
 */
export function pushStatusPill(subtype: string, text: string): string {
  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype, timestamp: Date.now() }])

  return id
}

interface PendingPromptItem {
  text: string
  attachments?: ChatAttachment[]
  messageId?: string
}

export const $pendingPromptBatch = atom<PendingPromptItem[]>([])

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

export const $chatTurnInFlight = atom<boolean>(false)

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

registerStorageClearHandler(() => {
  conversationVoiceSink().cancel()
  $chatSessionId.set(null)
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

  if (first) {
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

  const attachments = pendingBatch.flatMap(p => p.attachments ?? [])

  const batchPayload = {
    session_id: sessionId,
    batch: [
      {
        text: pendingBatch.map(p => p.text).join('\n\n'),
        ...(attachments.length ? { attachments: attachments.map(a => ({ file_url: a.url, type: a.type })) } : {})
      }
    ]
  }

  const submitWithRetry = async (attempt = 0): Promise<void> => {
    const g = $gateway.get()

    if (!g || g.connectionState !== 'open') {
      $chatTurnInFlight.set(false)

      return
    }

    try {
      presentationPorts().setSpriteState('thinking')
      await g.request('prompt.submit', batchPayload)
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err)

      if (errMsg.includes('in-flight') && attempt < 3) {
        await sleep(50 * Math.pow(2, attempt))

        return submitWithRetry(attempt + 1)
      }

      // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
      markAssistantTerminal({ error: err instanceof Error ? err.message : getStrings().chat.sendFailed })
      presentationPorts().setSpriteState('idle', { force: true })
      $chatTurnInFlight.set(false)
    }
  }

  void submitWithRetry()
}

export function beginAssistantMessage(): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined
  const initialVoiceStatus = conversationVoiceSink().isActive() ? 'pending' : undefined

  // 复用无内容的流式气泡，避免出现空白占位。
  if (lastItem?.role === 'assistant' && lastBody?.streaming) {
    if (!lastBody.text.trim() && !lastBody.toolName && !lastBody.error && !lastBody.cancelled) {
      if (initialVoiceStatus && lastBody.voiceStatus !== initialVoiceStatus) {
        $chatMessageBodies.setKey(lastItem.id, { ...lastBody, voiceStatus: initialVoiceStatus })
      }

      return
    }

    finalizeAssistantMessage()
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, { text: '', streaming: true, toolName: null, voiceStatus: initialVoiceStatus })
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

export function appendAssistantDelta(text: string, speechStyle?: SpeechStyle): void {
  // 仅更新当前流式消息 body，不改动 list 引用；首个 delta 过滤前导空行，避免撑大气泡上方
  patchLastAssistant(body => {
    const streamingText = (body.streamingText ?? body.text) + text

    return {
      ...body,
      speechStyle: speechStyle ?? body.speechStyle,
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

export function finalizeAssistantMessage(
  text?: string,
  media?: ChatMediaItem[],
  reasoning?: string,
  options?: { synthesize?: boolean; speechStyle?: SpeechStyle }
): void {
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
  const speechStyle = options?.speechStyle ?? body.speechStyle

  const finalReasoning =
    (typeof reasoning === 'string' && reasoning.trim() ? reasoning : body.reasoning)?.trim() || undefined

  // 助手消息为空且无推理/工具/错误/取消/媒体时剪掉，避免空白气泡。
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

  const shouldSynth =
    (options?.synthesize ?? true) && conversationVoiceSink().isActive() && Boolean(speechText(finalStr))

  const nextVoiceStatus = shouldSynth
    ? 'pending'
    : options?.synthesize === false
      ? 'failed'
      : body.voiceStatus === 'pending'
        ? undefined
        : body.voiceStatus

  $chatMessageBodies.setKey(lastItem.id, {
    ...body,
    text: finalStr,
    streamingText: undefined,
    speechStyle,
    reasoning: finalReasoning,
    media: finalMedia,
    streaming: false,
    toolName: null,
    voiceDuration:
      body.voiceDuration ?? (finalStr ? conversationVoiceSink().cachedDuration(finalStr, speechStyle) : undefined),
    voiceStatus: nextVoiceStatus
  })
  $lastAssistantStreaming.set(false)

  if (shouldSynth) {
    conversationVoiceSink().synthesize(lastItem.id, finalStr)
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
  conversationVoiceSink().cancel()
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $lastAssistantStreaming.set(false)
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
}
