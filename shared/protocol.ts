/** 同步等待模型的请求不按耗时结束；握手、心跳和普通查询沿用各端默认期限。 */
export function isAiWaitingRpc(method: string): boolean {
  return [
    'session.compress_context',
    'command.dispatch',
    'companion.idle_expression',
    'companion.should_act',
    'memory.update',
    'tts.match_voice',
    'tts.design_voice',
    'tts.generate_voice_prompt',
    'tts.clone_voice'
  ].includes(method)
}

export function isAiWaitingApi(path: string, method = 'GET'): boolean {
  if (method.toUpperCase() !== 'POST') return false
  const pathname = path.split(/[?#]/, 1)[0]
  return [
    /^\/api\/media\/(?:stt|tts)$/,
    /^\/api\/sessions\/messages\/\d+\/voice\/\d+$/,
    /^\/api\/companion\/avatar(?:\/(?:from-image|prompt|adopt)|\/\d+\/fullbody\/(?:reference(?:\/(?:prompt|adopt))?|confirm|candidate\/\d+\/(?:analyze|accept)))?$/,
    /^\/api\/companion\/outfits(?:\/(?:prompt|adopt)|\/\d+\/(?:regenerate|prompt|adopt|confirm))?$/,
    /^\/api\/companion\/portrait\/confirm$/,
    /^\/api\/companion\/scenes\/(?:prompt|adopt|\d+\/adopt)$/,
    /^\/api\/companion\/actions\/design$/,
    /^\/api\/companion\/posts\/publications\/[^/]+\/query$/,
    /^\/api\/companion\/desktop-videos\/actions\/\d+\/external-prompt$/
  ].some(pattern => pattern.test(pathname))
}

/** 图像迭代意图：微调上一版，或从固定参考重新生成。与后端 ImageReviseMode 对齐。 */
export type ImageReviseMode = 'edit' | 'regenerate'

export interface SessionInfo {
  archived?: boolean
  pinned?: boolean
  /** 服务端是自由字符串；一级值有 'special' 与 'standard'。 */
  kind?: string
  id: string
  input_tokens: number
  last_active: number
  message_count: number
  output_tokens: number
  preview: null | string
  started_at: number
  title: null | string
  tool_call_count: number
  /** 固定记忆域与展示预设，不由 kind 推断。 */
  system_preset_id?: null | string
  /** 对应预设的侧边栏图标。 */
  system_preset_icon_key?: null | string
}

/** `system.list_presets` RPC 返回的精简元数据；body 不下发。 */
export interface SystemPresetSummary {
  id: string
  name: string
  description: string
  icon_key: string
}

export interface SystemPresetListResponse {
  presets: SystemPresetSummary[]
}

export type PersonaEditableField = 'name' | 'personality' | 'relationship' | 'speaking_style'

export interface PersonaResponse {
  definition_json: string
  is_complete: boolean
  personality_tags: string[]
  current_mood: string | null
}

export interface PersonaUpdateRequest {
  definition_json: string
  /** 编辑开始时的字段值；缺失字段使用 null，服务端在锁内校验后合并可编辑字段。 */
  expected_fields?: Partial<Record<PersonaEditableField, string | null>>
}

export type CharacterFeatureKey =
  | 'head_shape'
  | 'facial_features'
  | 'facial_surface'
  | 'head_identifiers'
  | 'body_shape'
  | 'proportions'
  | 'limbs_and_appendages'
  | 'body_surface'
export type CharacterFeatures = Record<CharacterFeatureKey, string>
export type CharacterOverrides = Partial<Record<CharacterFeatureKey, string | null>>
export type ExtractionStatus = 'pending' | 'running' | 'ready' | 'failed'

export interface CharacterCard {
  avatar_id: number
  revision: number
  features: CharacterFeatures
  automatic: CharacterFeatures
  overrides: CharacterOverrides
  status: ExtractionStatus
  portrait_status: ExtractionStatus
  body_status: ExtractionStatus
  error: string | null
}

export interface CharacterCardExtractRequest {
  expected_avatar_id: number
  expected_revision: number
}

export interface CharacterCardUpdateRequest extends CharacterCardExtractRequest {
  changes: CharacterOverrides
}

export interface MemoryEvidence {
  fingerprint: string
  message_id?: number
  session_id?: number
  quote?: string
  stance?: 'supports' | 'opposes'
  created_at?: string
  source_unavailable?: boolean
}

export interface MemoryItem {
  id: number
  system_preset_id: string
  content_version: number
  content: string
  context: string | null
  tags: string | null
  basis: 'explicit' | 'inferred' | 'observed' | 'system'
  status: 'active' | 'candidate' | 'invalidated' | 'forgotten'
  usage: 'contextual' | 'background'
  reason: string
  source_kind: string
  evidence: MemoryEvidence[]
  importance: number
  created_at: string
  updated_at: string
  expires_at: string | null
  reviewed_at: string | null
}

export interface MemoryCounts {
  active: number
  candidate: number
  invalidated: number
  expired: number
  user_profile: number
}

export interface MemoryListRequest {
  system_preset_id?: string
  session_id?: string
  kind?: 'user_profile' | 'recall' | 'diary' | 'reflection' | 'interaction_stats'
  status?: 'active' | 'candidate' | 'invalidated' | 'expired'
  tag?: string
  q?: string
  offset?: number
  limit?: number
}

export interface MemoryListResponse {
  memories: MemoryItem[]
  counts: MemoryCounts
  total: number
  system_preset_id: string
  session_id?: string | null
}

export type UserProfileField = 'user_call_name' | 'user_gender' | 'user_birthday' | 'user_hobbies' | 'user_freeform'

export interface MemoryProfileChange {
  field: UserProfileField
  value: string | null
  /** 0 表示编辑开始时没有有效资料槽位。 */
  expected_version: number
}

export interface MemoryProfileUpdateRequest {
  system_preset_id: 'companion'
  changes: MemoryProfileChange[]
}

export interface MemoryProfileUpdateResponse {
  memories: MemoryItem[]
  system_preset_id: 'companion'
}

/** 助手消息附带的生成媒体；与正文正交，仅渲染端消费。 */
export interface ChatMediaItem {
  type: 'image' | 'video' | 'audio'
  url: string
  audio_url?: string
  review_id?: string
}

/** 用户侧聊天附件：图片为 data URL，音视频为后端上传返回的会话级 URL；水合与发送共用同一形状。 */
export interface ChatAttachment {
  type: 'image' | 'video' | 'audio'
  url: string
  duration?: number
  transcript?: string
}

export interface ReplyAudio {
  url: string
  duration: number
}

/** 最近成功回合的直接模型调用汇总；Token 合计仅包含有效报告缓存用量的请求。 */
export interface CacheUsageSummary {
  request_count: number
  reported_request_count: number
  input_tokens: number | null
  cached_input_tokens: number | null
  cache_hit_rate: number | null
}

export interface VideoQueueState {
  state: 'waiting' | 'rate_limited' | 'blocked'
  next_retry_at: string | null
}

export interface CompanionMediaBubble {
  type: 'image' | 'video'
  media_id: string
  status: 'pending' | 'ready' | 'failed' | 'result_unknown'
  url: string | null
  error: string | null
  video_queue?: VideoQueueState | null
}
export interface CompanionLinkBubble {
  type: 'link'
  topic_id: number
  title: string
  platform: 'x' | 'xiaohongshu' | 'douyin'
  url: string
  published_at: string
}

export type TopicFeedbackValue = 'like' | 'less' | null

export interface TopicFeedbackSnapshot {
  topic_id: number
  value: TopicFeedbackValue
  updated_at: string | null
}

/** 链接卡仅打开对应平台的 HTTPS 原文，持久数据也必须经过此边界。 */
export function companionLinkUrl(bubble: CompanionLinkBubble): string | null {
  try {
    const url = new URL(bubble.url)
    const domains = {
      x: ['x.com', 'twitter.com'],
      xiaohongshu: ['xiaohongshu.com'],
      douyin: ['douyin.com']
    }[bubble.platform]

    return url.protocol === 'https:' &&
      !url.username &&
      !url.password &&
      domains?.some(domain => url.hostname === domain || url.hostname.endsWith(`.${domain}`))
      ? url.href
      : null
  } catch {
    return null
  }
}

export function isTopicFeedbackSnapshot(value: unknown): value is TopicFeedbackSnapshot {
  if (
    typeof value !== 'object' ||
    value === null ||
    !('topic_id' in value) ||
    !('value' in value) ||
    !('updated_at' in value)
  ) {
    return false
  }

  return (
    typeof value.topic_id === 'number' &&
    Number.isInteger(value.topic_id) &&
    value.topic_id > 0 &&
    (value.value === 'like' || value.value === 'less' || value.value === null) &&
    (value.updated_at === null ||
      (typeof value.updated_at === 'string' && Number.isFinite(Date.parse(value.updated_at))))
  )
}

export function topicFeedbackIsCurrent(incoming: TopicFeedbackSnapshot, current?: TopicFeedbackSnapshot): boolean {
  if (!current || current.updated_at === null) {
    return true
  }

  if (incoming.updated_at === null) {
    return false
  }

  const difference = Date.parse(incoming.updated_at) - Date.parse(current.updated_at)

  if (difference !== 0) {
    return difference > 0
  }

  // PostgreSQL 时间保留微秒；Date.parse 只到毫秒，继续比较小数部分避免旧回包覆盖。
  const fraction = (timestamp: string): string =>
    (timestamp.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1] ?? '').padEnd(9, '0')

  return fraction(incoming.updated_at) >= fraction(current.updated_at)
}

export type CompanionBubble =
  | { type: 'text'; text: string }
  | { type: 'voice'; text: string; audio: ReplyAudio | null }
  | CompanionMediaBubble
  | CompanionLinkBubble

interface SessionMessageFields {
  /** 服务端确认该消息可作为最新用户回合的回答更换。 */
  can_replace_reply?: boolean
  content: unknown
  context?: unknown
  /** 数据库消息 ID，供编辑、派生与撤回定位。 */
  id?: number
  media?: ChatMediaItem[]
  name?: string
  reasoning?: null | string
  role: 'assistant' | 'system' | 'tool' | 'user'
  subtype?: string
  text?: unknown
  timestamp?: number
  tool_call_id?: null | string
  tool_calls?: unknown
  tool_name?: string
}

export type SessionMessage = SessionMessageFields &
  (
    | {
        content_type: 'companion_reply'
        bubbles: CompanionBubble[]
        role: 'assistant'
      }
    | { content_type: 'multimodal_v1' | 'text'; bubbles?: never }
  )

/** `session.undo_to_message` RPC 的 anchor 子类型：撤回后服务端把锚点消息的用户正文与图片退回客户端输入框。 */
export interface UndoAnchor {
  /** 用户正文，不含附件。 */
  text: string
  /** 可重新附加的图片（data URL）；视频随撤回清理，不恢复。 */
  attachments?: ChatAttachment[]
}

/** `session.undo_to_message` RPC 的返回形态。 */
export interface UndoResponse {
  session_id: string
  deleted_count: number
  anchor: UndoAnchor
  /** 截断后的完整消息列表，供前端 hydrate 替换本地状态。 */
  messages: SessionMessage[]
}

export interface ActiveTurnSnapshot {
  replace_reply_message_id?: number | null
  request_id: string
  origin_kind: string
  message_ids: number[]
  /** 正在逐泡交付的助手消息，恢复结构化气泡时沿用稳定身份。 */
  assistant_message_id?: number | null
  messages?: SessionMessage[]
  text: string
  reasoning: string
  bubbles: CompanionBubble[]
  tools: { name?: string; call_id?: string; status?: string }[]
  running: boolean
}

export interface PromptSubmissionState {
  replace_reply_message_id?: number | null
  result_message_id?: number | null
  request_id: string
  status: string
  error?: string | null
  message_ids?: number[]
  retry_message_id?: number | null
}

export interface PromptSubmissionResult extends PromptSubmissionState {
  queued?: boolean
  messages?: SessionMessage[]
}

export interface SessionResumeResponse {
  history_revision?: number
  last_submission?: PromptSubmissionState | null
  incremental?: boolean
  stream_id?: string
  active_turn?: ActiveTurnSnapshot | null
  info?: SessionRuntimeInfo
  message_count: number
  messages: SessionMessage[]
  session_id: string
  resumed?: boolean
  replayed_count?: number
  current_seq?: number
  truncated?: boolean
  next_cursor?: null | string
}

export interface SessionRuntimeInfo {
  system_preset_id: string
  is_automation?: boolean
  /** 客户端会话权限与语音入口的权威判定源，避免依赖尚未加载的会话列表。 */
  kind?: 'special' | 'standard' | (string & {})
  model?: string
  provider?: string
  running?: boolean
  settings?: Record<string, unknown>
  context_window?: null | number
}

/** `/api/config` 读写共用的 config 结构：GET 响应与 PUT 请求都以 `{ config }` 包裹，PUT 只需携带要改的字段。 */
export interface SpiritAgentConfigResponse {
  agent?: {
    reasoning_effort?: string
    enable_background_review?: boolean
    temperature?: number
  }
  chat?: {
    enable_context_compression?: boolean
    context_compression_threshold?: number
    title_generation_temperature?: number
    compression_temperature?: number
  }
  stt?: {
    enabled?: boolean
  }
  voice?: {
    /** 单次语音录制的最长时长（秒）。聊天面板在该上限处自动停止 MediaRecorder。 */
    max_recording_seconds?: number
  }
}

/** 手机网页的一次性扫码授权；url 仅在创建响应提供。 */
export interface RemotePairing {
  id: number
  url?: string
  expires_at: string
  state: 'pending' | 'paired' | 'cancelled' | 'expired'
}

export interface RemoteDevice {
  id: number
  name: string
  expires_at: string
  last_seen_at: string
  created_at: string
}
