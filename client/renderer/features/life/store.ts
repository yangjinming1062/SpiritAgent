import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import type { LifePursuit, LifeState } from './types'

export const $lifeRevision = atom(0)
export const $lifeState = atom<LifeState | null>(null)
export const $lifeFocusedPursuit = atom<LifePursuit | null>(null)
export const $lifeLoading = atom(false)
export const $lifeError = atom(false)

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

registerStorageClearHandler(() => {
  refreshRequest = null
  refreshQueued = false
  $lifeRevision.set(0)
  $lifeState.set(null)
  $lifeFocusedPursuit.set(null)
  $lifeLoading.set(false)
  $lifeError.set(false)
})
