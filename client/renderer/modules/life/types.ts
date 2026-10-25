import type { ApiSchemas } from '../../../../shared/api.generated'

export type LifeState = ApiSchemas['LifeStateView']
export type LifePursuit = ApiSchemas['LifePursuitView']
export type LifeEvent = ApiSchemas['LifeEventView']

export interface LifeTimelineEntry {
  id: number
  kind: 'activity' | 'started' | 'progressed' | 'completed' | 'visual' | 'post' | 'diary'
  text: string
  occurredAt: string
  pursuitId: number | null
  visualId: number | null
  postId: string
  diaryId: string
}
