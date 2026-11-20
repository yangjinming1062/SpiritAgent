import type { ApiSchemas } from './api.generated'
import { isRecord } from './is-record'
import { compareResourceIds, isResourceId } from './resource-id'

export type LifeEpisode = ApiSchemas['LifeTrajectoryEntry']
type LifeStep = ApiSchemas['LifeStepPublicView']
type MaterialKind = 'outfit' | 'scene' | 'action'
type LifeResult = { id: string; kind: MaterialKind } | { id: string; kind: 'visual'; versionId: string }

type EpisodePosition = Pick<LifeEpisode, 'id' | 'occurred_at'>

// PostgreSQL 时间戳保留微秒，Date.parse 只保留到毫秒。
function submillis(timestamp: string): number {
  return Number((timestamp.match(/\.(\d+)/)?.[1] ?? '').padEnd(6, '0').slice(3, 6))
}

export function compareLifeEpisodes(a: EpisodePosition, b: EpisodePosition): number {
  return (
    Date.parse(b.occurred_at) - Date.parse(a.occurred_at) ||
    submillis(b.occurred_at) - submillis(a.occurred_at) ||
    compareResourceIds(b.id, a.id)
  )
}

export function groupLifeEpisodes(episodes: readonly LifeEpisode[]): Array<{ date: string; entries: LifeEpisode[] }> {
  const days = new Map<string, LifeEpisode[]>()
  const sorted = [...episodes].sort(compareLifeEpisodes)

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

export function lifeResultKey(item: LifeResult): string {
  return item.kind === 'visual' ? 'visual/' + item.id + '/' + item.versionId : item.kind + '/' + item.id
}

export function lifeResults(reference: LifeEpisode['result_ref']): LifeResult[] {
  const results: LifeResult[] = []
  const references = Array.isArray(reference.results) ? [reference, ...reference.results.filter(isRecord)] : [reference]
  for (const ref of references) {
    if (isResourceId(ref.visual_id) && isResourceId(ref.visual_version_id)) {
      results.push({ id: ref.visual_id, kind: 'visual', versionId: ref.visual_version_id })
    }
    for (const kind of ['outfit', 'scene', 'action'] as const) {
      const id = ref[kind + '_id']
      if (isResourceId(id)) {
        results.push({ id, kind })
      }
    }
  }
  return [...new Map(results.map(item => [lifeResultKey(item), item])).values()]
}
