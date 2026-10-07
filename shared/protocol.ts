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

/** 助手消息附带的生成媒体；与正文正交，仅渲染端消费。 */
export interface ChatMediaItem {
  type: 'image' | 'video' | 'audio'
  url: string
  audio_url?: string
  review_id?: string
}

/** 用户侧聊天附件：图片为 data URL，视频为后端上传返回的会话级 URL；水合与发送共用同一形状。 */
export interface ChatAttachment {
  type: 'image' | 'video'
  url: string
}

export interface ReplyAudio {
  url: string
  duration: number
}
export interface CompanionMediaBubble {
  type: 'image' | 'video'
  media_id: string
  status: 'pending' | 'ready' | 'failed' | 'result_unknown'
  url: string | null
  error: string | null
}
export type CompanionBubble =
  | { type: 'text'; text: string }
  | { type: 'voice'; text: string; audio: ReplyAudio | null }
  | CompanionMediaBubble

interface SessionMessageFields {
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
  request_id: string
  origin_kind: string
  message_ids: number[]
  messages?: SessionMessage[]
  text: string
  reasoning: string
  bubbles: CompanionBubble[]
  tools: { name?: string; call_id?: string; status?: string }[]
  running: boolean
}

export interface PromptSubmissionState {
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
  context_window?: number
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
