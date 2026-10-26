import { compareResourceIds, isResourceId } from '../../../../shared/resource-id'

import type { LifeEvent, LifeTimelineEntry } from './types'

function textField(value: unknown, key: string): string {
  if (!value || typeof value !== 'object' || !(key in value)) {
    return ''
  }

  const field: unknown = Reflect.get(value, key)

  return typeof field === 'string' ? field.trim() : ''
}

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
    let text: string
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
        text = [textField(event.fact, 'title'), textField(event.fact, 'reason')].filter(Boolean).join(' · ')

        break

      case 'life.step.completed':

      case 'life.step.failed':

      case 'life.step.unknown':
        kind = event.kind === 'life.step.completed' ? 'step' : event.kind === 'life.step.failed' ? 'failed' : 'unknown'
        text = textField(event.fact, 'description')

        break

      case 'life.state.changed':
        kind = 'activity'
        text = textField(event.fact, 'current_activity')

        break

      case 'life.pursuit.started':
        kind = 'started'
        text = textField(event.fact, 'title')

        break

      case 'life.pursuit.progressed':
        kind = 'progressed'
        text = textField(event.fact, 'progress') || textField(event.fact, 'title')
        priority = 2

        break

      case 'life.pursuit.completed':
        kind = 'completed'
        text = textField(event.fact, 'progress') || textField(event.fact, 'title')
        priority = 3

        break

      case 'visual.ready':
        kind = 'visual'
        text = ''

        break

      case 'post.published':
        kind = 'post'
        text = ''

        break

      case 'diary.published':
        kind = 'diary'
        text = textField(event.fact, 'title')

        break

      default:
        continue
    }

    if (!text && ['activity', 'started', 'progressed', 'completed'].includes(kind)) {
      continue
    }

    const visual = textField(event.result_ref, 'visual_id')
    const visualId = isResourceId(visual) ? visual : null
    const postId = kind === 'post' ? textField(event.result_ref, 'entry_id') : ''
    const diaryId = kind === 'diary' ? textField(event.result_ref, 'entry_id') : ''

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
