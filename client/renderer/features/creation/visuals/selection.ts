import type { VisualsPresentation } from './types'

export type CatalogEntry = VisualsPresentation['ambient_catalog'][number]
type Intent = VisualsPresentation['intent']

export function motionAllowed(entry: CatalogEntry, subtle: boolean): boolean {
  const actual = entry.media_semantics

  return Boolean(
    actual?.auto_selectable &&
    (!subtle || (entry.motion_intensity === 'subtle' && actual.motion_intensity === 'subtle'))
  )
}

/** 条件来自实际视频检查；未知环境不视为已具备所需场景或道具。 */
export function contextAllowed(entry: CatalogEntry, intent: Intent): boolean {
  const actual = entry.media_semantics
  const sceneTags = intent?.scene_tags ?? []
  const activity = intent?.activity_kind === 'intent' ? 'idle' : (intent?.activity_type ?? 'idle')

  return Boolean(
    actual &&
    !actual.avoid_activity_types.includes(activity) &&
    !actual.avoid_scene_tags.some(tag => sceneTags.includes(tag)) &&
    actual.scene_tags.every(tag => sceneTags.includes(tag))
  )
}

/** 最近使用回避：轮换优先选择最久未出现的循环，避免在少数片段间抖动。 */
export function leastRecentlyUsed(
  candidates: readonly CatalogEntry[],
  recent: ReadonlyMap<string, number>
): CatalogEntry | null {
  let best: CatalogEntry | null = null
  let bestSeen = Number.POSITIVE_INFINITY

  for (const entry of candidates) {
    const seen = recent.get(`${entry.visual_id}:${entry.version}`) ?? 0

    if (seen < bestSeen) {
      best = entry
      bestSeen = seen
    }
  }

  return best
}
