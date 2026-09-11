import type { SpeechStyle } from '@ipc/contracts'

export interface SessionInfo {
  archived?: boolean
  pinned?: boolean
  /** 服务端是自由字符串；一级值有 'special'、'standard' 与 'im'（IM 桥接会话，桌面端只读）。 */
  kind?: string
  cwd?: null | string
  ended_at: null | number
  id: string
  _lineage_root_id?: null | string
  input_tokens: number
  is_active: boolean
  last_active: number
  message_count: number
  model: null | string
  output_tokens: number
  preview: null | string
  source: null | string
  started_at: number
  title: null | string
  tool_call_count: number
  /** 系统预设 id（5 套之一）；NULL = 用户普通对话，chat 时按 resolve_preset 降级到 companion。 */
  system_preset_id?: null | string
  /** 与 system_preset_id 对应的 icon_key；NULL 时降级为 companion.icon_key，供侧边栏图标直接渲染。 */
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
}

/** 用户侧聊天附件：图片为 data URL，视频为后端上传返回的会话级 URL；水合与发送共用同一形状。 */
export interface ChatAttachment {
  type: 'image' | 'video'
  url: string
}

export interface SessionMessage {
  speech_style?: SpeechStyle
  codex_reasoning_items?: unknown
  content: unknown
  context?: unknown
  /** 后端 DB row id（build_session_messages(include_id=True) 下发）；用于 fork / undo 按钮回传给后端的 source_message_id。 */
  id?: number
  media?: ChatMediaItem[]
  name?: string
  reasoning?: null | string
  reasoning_content?: null | string
  reasoning_details?: unknown
  role: 'assistant' | 'system' | 'tool' | 'user'
  subtype?: string
  text?: unknown
  timestamp?: number
  tool_call_id?: null | string
  tool_calls?: unknown
  tool_name?: string
}

/** `session.undo_to_message` RPC 的 anchor 子类型：撤回后服务端把锚点行的载荷推回客户端落输入框。 */
export interface UndoAnchor {
  /** 锚点行 content（多模态形态以 JSON parts 字符串承载；纯文本则直接是文本）。 */
  text: string
  /** 与 Message.content_type 对齐：text 或 multimodal_v1。 */
  content_type?: string
  /** 与 Message.media_json 对齐：助手媒体时存在；用户行通常为 null。 */
  media_json?: string | null
}

/** `session.undo_to_message` RPC 与对应 REST 端点的返回形态。 */
export interface UndoResponse {
  session_id: string
  deleted_count: number
  anchor: UndoAnchor
  /** 截断后的完整消息列表，供前端 hydrate 替换本地状态。 */
  messages: SessionMessage[]
}

export interface SessionResumeResponse {
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
  branch?: string
  cwd?: string
  /** 客户端 IM 守卫与语音入口的权威判定源，避免依赖尚未加载的会话列表。 */
  kind?: 'im' | 'special' | 'standard' | (string & {})
  model?: string
  provider?: string
  running?: boolean
  settings?: Record<string, unknown>
  context_window?: number
}

/** `GET /api/config` 的返回结构。*/
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

/** `PUT /api/config` 接受的请求体。 */
export interface SpiritAgentConfigPutRequest {
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
    max_recording_seconds?: number
  }
}

/** IM 通道桥（/api/channels）——绑定状态视图；凭据字段服务端永不回显。 */
export interface ChannelCapabilities {
  supports_typing: boolean
  supports_media: boolean
  can_initiate: boolean
  requires_login: boolean
}

export interface ChannelBindingInfo {
  status: string
  account_ref: string
  account_name: string
  conversation_id: number | null
  last_error: string | null
  updated_at: string | null
}

export interface ChannelInfo {
  channel: string
  title: string
  capabilities: ChannelCapabilities
  binding: ChannelBindingInfo | null
}

export interface ChannelListResponse {
  items: ChannelInfo[]
}

/** 扫码登录轮询：state ∈ idle|wait|scaned|confirmed|expired|error|login_required|connected。 */
export interface ChannelLoginState {
  state: string
  qr_image?: string | null
  error?: string | null
}

export interface ChannelPeerInfo {
  peer_id: string
  peer_name: string
  status: 'allowed' | 'blocked' | 'pending'
  last_message_at: string | null
}

export interface ChannelPeersResponse {
  items: ChannelPeerInfo[]
}

export type ChannelPeerAction = 'approve' | 'block' | 'delete'
