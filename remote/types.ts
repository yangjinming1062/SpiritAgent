import type { ActiveTurnSnapshot, VisualPlaybackReceipt } from '@protocol'

import type { ApiSchemas } from '../shared/api.generated'

export type { ChatAttachment, CompanionBubble, SessionInfo, SessionMessage, SystemPresetSummary } from '@protocol'

export type RemoteSession = ApiSchemas['RemoteSessionResponse']

export type ActiveTurn = ActiveTurnSnapshot
export type Post = ApiSchemas['PostResponse']

export type Recovery = ApiSchemas['PostPublicationRecovery']

export type Diary = ApiSchemas['DiaryEntryResponse']

export type Scene = ApiSchemas['SceneResponse']

export type Outfit = ApiSchemas['OutfitResponse']

/** 桌面回执事件在本端的留痕；回执只由桌面提交，手机仅展示。 */
export interface PlaybackReceipt extends VisualPlaybackReceipt {
  at: number
}
