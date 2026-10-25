import type { ApiSchemas } from '../../../../shared/api.generated'

export type LifeState = ApiSchemas['LifeStateView']
export type LifePursuit = ApiSchemas['LifePursuitView']
export type LifeEvent = ApiSchemas['LifeEventView']

export interface LifeTimelineEntry {
  id: string
  kind: 'activity' | 'started' | 'progressed' | 'completed' | 'visual' | 'post' | 'diary'
  text: string
  occurredAt: string
  pursuitId: string | null
  visualId: string | null
  postId: string
  diaryId: string
}
