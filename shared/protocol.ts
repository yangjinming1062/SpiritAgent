import type { ApiOperations, ApiSchemas } from './api.generated'
import { SOCIAL_PLATFORMS } from './api.generated'
import { isResourceId } from './resource-id'

/** 图像迭代意图：微调上一版，或从固定参考重新生成。与后端 ImageReviseMode 对齐。 */
export type ImageReviseMode = 'edit' | 'regenerate'

export type SessionInfo = ApiSchemas['DesktopSessionInfo']

/** 预设目录仅下发公开元数据。 */
export type SystemPresetSummary = ApiSchemas['PromptPresetSummary']

export type PersonaEditableField = keyof NonNullable<ApiSchemas['PersonaUpdate']['expected_fields']>

export type PersonaResponse = ApiSchemas['PersonaResponse']

export type PersonaUpdateRequest = ApiSchemas['PersonaUpdate']

export type CharacterFeatureKey = keyof ApiSchemas['CharacterFeatures']
export type CharacterFeatures = ApiSchemas['CharacterFeatures']
export type CharacterOverrides = ApiSchemas['CharacterOverridesInput']

export type CharacterCard = ApiSchemas['CharacterCardResponse']

export type MemoryItem = ApiSchemas['MemoryListItem']
export type MemoryListResponse = ApiSchemas['MemoryListResponse']
export type MemoryDetail = ApiSchemas['MemoryDetail']

export type MemoryCounts = ApiSchemas['MemoryListResponse']['counts']

export type UserProfileField = ApiSchemas['MemoryProfileChange']['field']

export type MemoryProfileChange = ApiSchemas['MemoryProfileChange']

export type MemoryProfileUpdateRequest = ApiSchemas['MemoryProfileRequest']

/** 助手消息附带的生成媒体；与正文正交，仅渲染端消费。 */
export type ChatMediaItem = Pick<ApiSchemas['ClientMediaEntry'], 'type' | 'url'> &
  Partial<Omit<ApiSchemas['ClientMediaEntry'], 'type' | 'url'>>

/** 用户侧聊天附件：图片为 data URL，音视频为后端上传返回的会话级 URL；水合与发送共用同一形状。 */
export interface ChatAttachment {
  type: 'image' | 'video' | 'audio'
  url: string
  duration?: number
  transcript?: string
}

export type ReplyAudio = ApiSchemas['RpcReplyAudio']

/** 最近成功回合的直接模型调用汇总；Token 合计仅包含有效报告缓存用量的请求。 */
export type CacheUsageSummary = ApiSchemas['RpcCacheUsage']

export type VideoQueueState = ApiSchemas['RpcVideoQueueInfo']

export type CompanionMediaBubble = ApiSchemas['RpcClientMediaBubble']
export type CompanionLinkBubble = ApiSchemas['RpcLinkBubble']

export type TopicFeedbackValue = 'like' | 'less' | null

export type TopicFeedbackSnapshot = ApiSchemas['TopicFeedbackSnapshot']

/** 链接卡仅打开对应平台的 HTTPS 原文，持久数据也必须经过此边界。 */
export function companionLinkUrl(bubble: CompanionLinkBubble): string | null {
  try {
    const url = new URL(bubble.url)
    const hosts = SOCIAL_PLATFORMS[bubble.platform].hosts

    return url.protocol === 'https:' &&
      !url.username &&
      !url.password &&
      !url.port &&
      !url.hash &&
      hosts.some(host => url.hostname === host)
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
    isResourceId(value.topic_id) &&
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

export type CompanionBubble = ApiSchemas['RpcReplyBubbleView']

export type SessionMessage = ApiSchemas['RpcSessionMessageView']

/** 撤回后把锚点消息的用户正文与图片退回客户端输入框。 */
export type UndoResponse = ApiSchemas['SessionUndoResult']

export type ActiveTurnSnapshot = ApiSchemas['ActiveTurnView']

export type PromptSubmissionResult = ApiSchemas['PromptSubmitResult']

export type { SessionSnapshot } from './gateway'

export type SessionRuntimeInfo = ApiSchemas['SessionRuntimeInfo']

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
export type RemotePairing = ApiSchemas['PairingResponse']

export type RemoteDevice = ApiSchemas['DeviceResponse']

export type PromptSubmissionRequest = ApiSchemas['PromptSubmitRequest']

export type MemoryListRequest = ApiOperations['resource_catalog.get_memories']['query']

/**
 * 影像与动作的共享类型：字段以 backend 对外 DTO 的生成契约为准，仅覆盖手机消费面。
 */
export type VisualMediaKind = ApiSchemas['VisualItemView']['media_kind']
export type VisualAssetKind = ApiSchemas['VisualItemView']['asset_kind']
export type VisualJobState = ApiSchemas['VisualJobView']['state']
export type VisualJobCommand = ApiSchemas['VisualJobCommandRequest']['command']
export type PlaybackStatus = ApiSchemas['RpcVisualPlaybackEvent']['status']
export type PlaybackReceiptKind = NonNullable<ApiSchemas['RpcVisualPlaybackEvent']['receipt_kind']>
export type ActionClipKind = NonNullable<ApiSchemas['ActionAssetDesign']['clip_kind']>
export type ActionReviewStatus = ApiSchemas['ActionAssetView']['status']
export type ActionLibraryGroup = NonNullable<ApiOperations['companion_actions.list_actions']['query']['group']>
export type VisualItem = ApiSchemas['VisualItemView']
export type VisualJob = ApiSchemas['VisualJobView']
export type VisualDetail = ApiSchemas['VisualDetailView']
export type VisualRegenerateRequest = ApiSchemas['VisualRegenerateRequest']
export type VisualPresentationUpdate = ApiSchemas['VisualPreferencesRequest']
export type VisualJobCommandRequest = ApiSchemas['VisualJobCommandRequest']
export type VisualListResponse = ApiSchemas['VisualListResponse']
export type VisualJobListResponse = ApiSchemas['VisualJobListResponse']
export type ActionDefinition = ApiSchemas['ActionAssetView']
export type ActionDefinitionRequest = ApiSchemas['ActionAssetDesign']
export type ActionMediaEntry = ApiSchemas['ActionMediaEntry']
export type ActionMediaListResponse = ApiSchemas['ActionMediaListResponse']
export type ActionUpdateRequest = ApiSchemas['ActionAssetUpdate']
export type ActionListResponse = ApiSchemas['ActionListResponse']

export type VoiceSelectionResponse = ApiSchemas['CurrentVoiceResponse']
