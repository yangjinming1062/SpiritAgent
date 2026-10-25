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

const pursuits = createLifeResource<{ items: LifePursuit[]; total: number }>({ items: [], total: 0 })
const events = createLifeResource<{ items: LifeEvent[]; total: number }>({ items: [], total: 0 })
const detail = createLifeResource<LifePursuit | null>(null)

export const $lifePursuits = computed(pursuits.$data, value => value.items)
export const $lifePursuitsTotal = computed(pursuits.$data, value => value.total)
export const $lifePursuitsLoading = pursuits.$loading
export const $lifePursuitsError = pursuits.$error
export const $lifeEvents = computed(events.$data, value => value.items)
export const $lifeEventsTotal = computed(events.$data, value => value.total)
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

export function loadLifePursuits(status: 'all' | 'active' | 'completed', page: number): Promise<void> {
  return pursuits.load(`pursuits:${status}:${page}`, () =>
    requestResource('companion_life.get_life_pursuits', {
      query: { status: status === 'all' ? undefined : status, offset: page * LIFE_PAGE_SIZE, limit: LIFE_PAGE_SIZE }
    })
  )
}

export function loadLifeEvents(page: number, pursuitId?: number): Promise<void> {
  return events.load(`events:${pursuitId ?? ''}`, async current => {
    const items: LifeEvent[] = []
    const end = (page + 1) * LIFE_PAGE_SIZE
    let total = end

    // 累计已展开的范围再投影，同一结果跨接口分页时仍只展示一次。
    while (items.length < Math.min(end, total) && current()) {
      const response = await requestResource('companion_life.get_life_events', {
        query: { pursuit_id: pursuitId, offset: items.length, limit: Math.min(50, end - items.length) }
      })

      total = response.total

      items.push(...response.items)

      if (response.items.length === 0) {
        break
      }
    }

    return { items, total }
  })
}

export function loadLifeDetail(pursuitId: number | null): Promise<void> {
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
