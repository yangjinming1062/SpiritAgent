import type { ActiveTurnSnapshot } from '@protocol'

import type { ApiSchemas } from '../shared/api.generated'

export type { ChatAttachment, CompanionBubble, SessionInfo, SessionMessage, SystemPresetSummary } from '@protocol'

export type RemoteSession = ApiSchemas['RemoteSessionResponse']

export type ActiveTurn = ActiveTurnSnapshot
export type Post = ApiSchemas['PostResponse']

export type Recovery = ApiSchemas['PostPublicationRecovery']

export type Diary = ApiSchemas['DiaryEntryResponse']

export type Scene = ApiSchemas['SceneResponse']

export type Outfit = ApiSchemas['OutfitResponse']

export type VideoPack = ApiSchemas['VideoPackResponse']
