import { type ArchiveSearchState, isArchiveSearchState } from './archive-search'
import { isRecord } from './is-record'
import { isResourceId } from './resource-id'

export const pursuitFilters = ['all', 'active', 'paused', 'completed', 'abandoned'] as const
export type PursuitFilter = (typeof pursuitFilters)[number]

export type LifeDestination =
  | { kind: 'home' }
  | { kind: 'pursuits'; status: PursuitFilter }
  | { kind: 'pursuit' | 'topic'; id: string }
  | { kind: 'search'; search: ArchiveSearchState }

export interface LifeEpisodeBoundary {
  page: number
  id: string
  occurred_at: string
}

export type LifeFrame = LifeDestination & { page: number; scrollTop: number; boundary?: LifeEpisodeBoundary }
export interface LifeReaderState {
  frames: LifeFrame[]
  /** 普通菜单回到首页后，显式重入同一分支时接续其筛选和分页。 */
  suspended?: LifeFrame[]
}

export function initialLifeReader(): LifeReaderState {
  return { frames: [{ kind: 'home', page: 0, scrollTop: 0 }] }
}

export function lifePlaceKey(place: LifeDestination): string {
  return 'id' in place ? place.kind + '/' + place.id : place.kind
}

export function visitLife(state: LifeReaderState, place: LifeDestination): LifeReaderState {
  const existing = state.frames.findIndex(frame => lifePlaceKey(frame) === lifePlaceKey(place))
  if (existing >= 0 && place.kind !== 'search') {
    return { frames: state.frames.slice(0, existing + 1) }
  }
  const [saved, ...rest] = state.suspended ?? []
  if (saved && lifePlaceKey(saved) === lifePlaceKey(place)) {
    return { frames: [...state.frames, saved], suspended: rest }
  }
  return { frames: [...state.frames, { ...place, page: 0, scrollTop: 0 }] }
}

export function isLifeReaderState(value: unknown): value is LifeReaderState {
  if (
    !isRecord(value) ||
    !Array.isArray(value.frames) ||
    value.frames[0]?.kind !== 'home' ||
    (value.suspended !== undefined && !Array.isArray(value.suspended))
  ) {
    return false
  }
  return [...value.frames, ...(value.suspended ?? [])].every((frame, index, frames) => {
    if (
      !isRecord(frame) ||
      typeof frame.page !== 'number' ||
      !Number.isSafeInteger(frame.page) ||
      frame.page < 0 ||
      typeof frame.scrollTop !== 'number' ||
      !Number.isFinite(frame.scrollTop) ||
      frame.scrollTop < 0
    ) {
      return false
    }
    const boundary = frame.boundary
    if (
      boundary !== undefined &&
      (typeof frame.kind !== 'string' ||
        !['home', 'topic', 'pursuit'].includes(frame.kind) ||
        !isRecord(boundary) ||
        !isResourceId(boundary.id) ||
        typeof boundary.occurred_at !== 'string' ||
        !Number.isFinite(Date.parse(boundary.occurred_at)) ||
        typeof boundary.page !== 'number' ||
        !Number.isSafeInteger(boundary.page) ||
        boundary.page <= 0 ||
        boundary.page > frame.page)
    ) {
      return false
    }
    switch (frame.kind) {
      case 'home':
        return index === 0
      case 'pursuits':
        return pursuitFilters.some(status => status === frame.status)
      case 'topic':
      case 'pursuit':
        return isResourceId(frame.id)
      case 'search':
        return (
          index > 0 &&
          ['home', 'topic', 'pursuit'].includes(frames[index - 1]?.kind) &&
          isArchiveSearchState(frame.search)
        )
      default:
        return false
    }
  })
}
