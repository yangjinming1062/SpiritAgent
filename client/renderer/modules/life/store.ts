import { atom, computed } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import type { LifeEvent, LifePursuit, LifeState } from './types'

export const $lifeRevision = atom(0)
export const $lifeState = atom<LifeState | null>(null)
export const $lifeFocusedPursuit = atom<LifePursuit | null>(null)
export const $lifeLoading = atom(false)
export const $lifeError = atom(false)

function createLifeResource<T>(fallback: T) {
  const $data = atom(fallback)
  const $loading = atom(false)
  const $error = atom(false)
  let request = 0
  let queryKey: string | null = null

  const clear = (): void => {
    request++
    queryKey = null
    $data.set(fallback)
    $loading.set(false)
    $error.set(false)
  }

  registerStorageClearHandler(clear)

  async function load(key: string, read: (current: () => boolean) => Promise<T>): Promise<void> {
    const scope = captureAuthScope()

    if (!scope) {
      return
    }

    const token = ++request
    const current = (): boolean => scope() && token === request

    if (key !== queryKey) {
      queryKey = key
      $data.set(fallback)
    }

    $loading.set(true)
    $error.set(false)

    try {
      const data = await read(current)

      if (current()) {
        $data.set(data)
      }
    } catch (error) {
      if (current()) {
        log.warn('life', `Could not load ${key}`, error)
        $error.set(true)
      }
    } finally {
      if (current()) {
        $loading.set(false)
      }
    }
  }

  return { $data, $loading, $error, load, clear }
}

interface LifePageData<T> {
  items: T[]
  has_more: boolean
  next_cursor: string | null
}
interface LifeEventPage extends LifePageData<LifeEvent> {
  page: number
  revision: number
}
const pursuits = createLifeResource<LifePageData<LifePursuit>>({ items: [], has_more: false, next_cursor: null })

const events = createLifeResource<LifeEventPage>({
  items: [],
  has_more: false,
  next_cursor: null,
  page: -1,
  revision: -1
})

const detail = createLifeResource<LifePursuit | null>(null)

export const $lifePursuits = computed(pursuits.$data, value => value.items)
export const $lifePursuitsHasMore = computed(pursuits.$data, value => value.has_more)
export const $lifePursuitsNextCursor = computed(pursuits.$data, value => value.next_cursor)
export const $lifePursuitsLoading = pursuits.$loading
export const $lifePursuitsError = pursuits.$error
export const $lifeEvents = computed(events.$data, value => value.items)
export const $lifeEventsHasMore = computed(events.$data, value => value.has_more)
export const $lifeEventsLoading = events.$loading
export const $lifeEventsError = events.$error
export const $lifeDetail = detail.$data
export const $lifeDetailLoading = detail.$loading
export const $lifeDetailError = detail.$error

export const LIFE_PAGE_SIZE = 20
let refreshQueued = false
let refreshRevision = -1
let refreshRequest: Promise<void> | null = null

export function handleLifeUpdated(): void {
  $lifeRevision.set($lifeRevision.get() + 1)
}

export function refreshLife(): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return Promise.resolve()
  }

  if (refreshRequest) {
    refreshQueued ||= refreshRevision !== $lifeRevision.get()

    return refreshRequest
  }

  refreshQueued = true

  const current = scope
  $lifeLoading.set(true)

  const request = (async (): Promise<void> => {
    while (refreshQueued && current()) {
      refreshQueued = false
      refreshRevision = $lifeRevision.get()

      try {
        const state = await requestResource('companion_life.get_life')

        if (!current() || refreshQueued) {
          continue
        }

        if (state.revision < ($lifeState.get()?.revision ?? -1)) {
          continue
        }

        $lifeState.set(state)

        if ($lifeFocusedPursuit.get()?.id !== state.active_pursuit_id) {
          $lifeFocusedPursuit.set(null)
        }

        const pursuit = state.active_pursuit_id
          ? await requestResource('companion_life.get_life_pursuit', { path: { pursuit_id: state.active_pursuit_id } })
          : null

        if (current() && !refreshQueued) {
          $lifeFocusedPursuit.set(pursuit)
          $lifeError.set(false)
        }
      } catch (error) {
        if (current() && !refreshQueued) {
          log.warn('life', 'Could not refresh life overview', error)
          $lifeError.set(true)
        }
      }
    }
  })().finally(() => {
    if (refreshRequest === request) {
      refreshRequest = null
    }

    if (current()) {
      $lifeLoading.set(false)
    }
  })

  refreshRequest = request

  return request
}

export function loadLifePursuits(status: 'all' | 'active' | 'completed', cursor: string | null): Promise<void> {
  return pursuits.load(`pursuits:${status}:${cursor ?? ''}`, () =>
    requestResource('companion_life.get_life_pursuits', {
      query: { status: status === 'all' ? undefined : status, cursor, limit: LIFE_PAGE_SIZE }
    })
  )
}

export function loadLifeEvents(page: number, pursuitId?: string): Promise<void> {
  return events.load(`events:${pursuitId ?? ''}`, async current => {
    const cached = events.$data.get()
    const revision = $lifeRevision.get()
    let items = cached.items
    let nextCursor = cached.next_cursor
    let hasMore = cached.has_more
    let loadedPage = cached.page

    if (loadedPage < 0 || cached.revision !== revision) {
      const response = await requestResource('companion_life.get_life_events', {
        query: { pursuit_id: pursuitId, limit: LIFE_PAGE_SIZE }
      })

      if (loadedPage < 0 || !response.has_more) {
        items = response.items
        nextCursor = response.next_cursor
        hasMore = response.has_more
        loadedPage = 0
      } else {
        // 事件为事实记录；刷新补齐头页，已展开的较早范围继续沿原游标读取。
        items = [...new Map([...cached.items, ...response.items].map(item => [item.id, item])).values()]
      }
    }

    while (loadedPage < page && hasMore && nextCursor && current()) {
      const response = await requestResource('companion_life.get_life_events', {
        query: { pursuit_id: pursuitId, cursor: nextCursor, limit: LIFE_PAGE_SIZE }
      })

      items = [...new Map([...items, ...response.items].map(item => [item.id, item])).values()]
      nextCursor = response.next_cursor
      hasMore = response.has_more
      loadedPage++
    }

    return { items, next_cursor: nextCursor, has_more: hasMore, page: loadedPage, revision }
  })
}

export function loadLifeDetail(pursuitId: string | null): Promise<void> {
  if (pursuitId === null) {
    detail.clear()

    return Promise.resolve()
  }

  return detail.load(`pursuit:${pursuitId}`, () =>
    requestResource('companion_life.get_life_pursuit', { path: { pursuit_id: pursuitId } })
  )
}

registerStorageClearHandler(() => {
  refreshRequest = null
  refreshQueued = false
  $lifeRevision.set(0)
  $lifeState.set(null)
  $lifeFocusedPursuit.set(null)
  $lifeLoading.set(false)
  $lifeError.set(false)
})
