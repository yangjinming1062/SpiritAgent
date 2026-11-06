import { compareResourceIds, isResourceId } from '../../../../shared/resource-id'

import type { LifeEvent, LifeTimelineEntry } from './types'

function numberField(value: unknown, key: string): number | null {
  if (!value || typeof value !== 'object' || !(key in value)) {
    return null
  }

  const field: unknown = Reflect.get(value, key)

  return typeof field === 'number' && Number.isSafeInteger(field) && field > 0 ? field : null
}

export function lifeTimeline(events: readonly LifeEvent[]): LifeTimelineEntry[] {
  const entries = new Map<string, { entry: LifeTimelineEntry; priority: number }>()

  for (const event of events) {
    if (event.subject !== 'companion' || !event.is_meaningful) {
      continue
    }

    let kind: LifeTimelineEntry['kind']
    const text = event.narrative?.trim() ?? ''
    let priority = 0

    switch (event.kind) {
      case 'life.pursuit.paused':

      case 'life.pursuit.active':

      case 'life.pursuit.abandoned':
        kind =
          event.kind === 'life.pursuit.paused'
            ? 'paused'
            : event.kind === 'life.pursuit.active'
              ? 'resumed'
              : 'abandoned'

        break

      case 'life.step.completed':

      case 'life.step.failed':

      case 'life.step.unknown':
        kind = event.kind === 'life.step.completed' ? 'step' : event.kind === 'life.step.failed' ? 'failed' : 'unknown'

        break

      case 'life.state.changed':
        kind = 'activity'

        break

      case 'life.pursuit.started':
        kind = 'started'

        break

      case 'life.pursuit.progressed':
        kind = 'progressed'
        priority = 2

        break

      case 'life.pursuit.completed':
        kind = 'completed'
        priority = 3

        break

      case 'visual.ready':
        kind = 'visual'

        break

      case 'post.published':
        kind = 'post'

        break

      case 'diary.published':
        kind = 'diary'

        break

      default:
        continue
    }

    const visual = typeof event.result_ref.visual_id === 'string' ? event.result_ref.visual_id : ''
    const visualId = isResourceId(visual) ? visual : null
    const postId = kind === 'post' && typeof event.result_ref.entry_id === 'string' ? event.result_ref.entry_id : ''
    const diaryId = kind === 'diary' && typeof event.result_ref.entry_id === 'string' ? event.result_ref.entry_id : ''

    const resultKey = visualId
      ? `visual:${visualId}:${numberField(event.result_ref, 'version') ?? ''}`
      : postId
        ? `post:${postId}`
        : diaryId
          ? `diary:${diaryId}`
          : event.step_id
            ? `step:${event.step_id}`
            : `event:${event.id}`

    const previous = entries.get(resultKey)

    if (!previous || priority > previous.priority) {
      entries.set(resultKey, {
        priority,
        entry: {
          id: event.id,
          kind,
          text,
          occurredAt: event.occurred_at,
          pursuitId: event.pursuit_id,
          visualId,
          postId,
          diaryId
        }
      })
    }
  }

  return [...entries.values()]
    .map(item => item.entry)
    .sort((a, b) => b.occurredAt.localeCompare(a.occurredAt) || compareResourceIds(b.id, a.id))
}
