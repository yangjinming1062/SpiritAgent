import type { ApiSchemas } from './api.generated'
import { compareResourceIds, isResourceId } from './resource-id'

export type LifeEpisode = ApiSchemas['LifeTrajectoryEntry']
type LifeStep = ApiSchemas['LifeStepPublicView']
type MaterialKind = 'outfit' | 'scene' | 'action'
type LifeResult = { id: string; kind: MaterialKind } | { id: string; kind: 'visual'; versionId: string }

export function groupLifeEpisodes(episodes: readonly LifeEpisode[]): Array<{ date: string; entries: LifeEpisode[] }> {
  const days = new Map<string, LifeEpisode[]>()
  const sorted = [...episodes].sort(
    (a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at) || compareResourceIds(b.id, a.id)
  )

  for (const episode of sorted) {
    const entries = days.get(episode.local_date)

    if (entries) {
      entries.push(episode)
    } else {
      days.set(episode.local_date, [episode])
    }
  }

  return [...days].map(([date, entries]) => ({ date, entries }))
}

export function formatLifeDay(day: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(day))
}

export function lifeEpisodeLabel(episode: LifeEpisode): 'exchange' | 'milestone' | 'reading' | 'result' | 'virtual' {
  if (episode.kind === 'result' && episode.reading_result) {
    return 'reading'
  }
  if (episode.kind === 'milestone' && episode.fact_type === 'execution') {
    return 'exchange'
  }

  return episode.kind
}

export function lifeStepStatus(
  step: LifeStep
): LifeStep['state'] | 'mediaPaused' | 'mediaQueued' | 'reviewPending' | 'permissionWait' {
  if (step.state === 'running' && step.disposition === 'waiting') {
    switch (step.failure_code) {
      case 'media_paused':
        return 'mediaPaused'
      case 'media_resume_waiting':
        return 'mediaQueued'
      case 'review_pending':
        return 'reviewPending'
      case 'permission_wait':
        return 'permissionWait'
    }
  }

  return step.state
}

export function lifeResults(reference: LifeEpisode['result_ref']): LifeResult[] {
  const results: LifeResult[] = []
  if (isResourceId(reference.visual_id) && isResourceId(reference.visual_version_id)) {
    results.push({ id: reference.visual_id, kind: 'visual', versionId: reference.visual_version_id })
  }
  const candidates: Array<[MaterialKind, unknown]> = [
    ['outfit', reference.outfit_id],
    ['scene', reference.scene_id],
    ['action', reference.action_id]
  ]

  for (const [kind, id] of candidates) {
    if (isResourceId(id)) {
      results.push({ id, kind })
    }
  }
  return results
}
