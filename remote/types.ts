import type { ActiveTurnSnapshot, SessionResumeResponse } from '@protocol'

import type { ApiSchemas } from '../shared/api.generated'

export type { ChatAttachment, CompanionBubble, SessionInfo, SessionMessage, SystemPresetSummary } from '@protocol'

export interface RemoteSession {
  user: { id: number; username: string }
  device: {
    id: number
    name: string
    expires_at: string
    last_seen_at: string | null
  }
  csrf_token: string
}

export type ActiveTurn = ActiveTurnSnapshot
export type Resume = SessionResumeResponse

export type History = ApiSchemas['SessionHistoryResponse']

export type Comment = ApiSchemas['PostCommentResponse']

export type Post = ApiSchemas['PostResponse']

export type Recovery = ApiSchemas['PostPublicationRecovery']

export type Diary = ApiSchemas['DiaryEntryResponse']

export type Scene = ApiSchemas['SceneResponse']

export type Outfit = ApiSchemas['OutfitResponse']

export type VideoPack = ApiSchemas['VideoPackResponse']
