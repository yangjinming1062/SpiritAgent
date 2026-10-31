import type { ApiSchemas } from '../../../../shared/api.generated'

export type LifeState = ApiSchemas['LifeStatePublicView']
export type LifePursuit = ApiSchemas['LifePursuitPublicView']
export type LifePursuitDetail = ApiSchemas['LifePursuitPublicDetail']
export type LifeEvent = ApiSchemas['LifeEventPublicView']

export interface LifeTimelineEntry {
  id: string
  kind:
    | 'activity'
    | 'started'
    | 'progressed'
    | 'completed'
    | 'visual'
    | 'post'
    | 'diary'
    | 'paused'
    | 'resumed'
    | 'abandoned'
    | 'step'
    | 'failed'
    | 'unknown'
  text: string
  occurredAt: string
  pursuitId: string | null
  visualId: string | null
  postId: string
  diaryId: string
}
