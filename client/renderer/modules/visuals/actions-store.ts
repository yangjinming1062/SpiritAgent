import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import { actionsApi } from './api'
import type { ActionDefinition, ActionDefinitionRequest, ActionUpdateRequest } from './types'

export const $actions = atom<ActionDefinition[]>([])
export const $actionsLoading = atom(false)
export const $actionsError = atom<string | null>(null)
export const $actionsNextCursor = atom<string | null>(null)

let epoch = 0
let refreshRequest: Promise<void> | null = null
let activeQuery = ''
let readRevision = 0

export async function refreshActions(query = activeQuery, append = false): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  if (refreshRequest && query === activeQuery) {
    return refreshRequest
  }

  const cursor = append ? $actionsNextCursor.get() : null

  if (append && !cursor) {
    return
  }

  activeQuery = query
  const revision = ++readRevision

  const currentEpoch = epoch
  $actionsLoading.set(true)

  const request = (async (): Promise<void> => {
    try {
      const list = await actionsApi.list({ query: query || undefined, cursor: cursor ?? undefined, limit: 50 })

      if (!scope?.() || epoch !== currentEpoch || revision !== readRevision) {
        return
      }

      const previous = $actions.get()
      $actions.set(
        append ? [...previous, ...list.actions.filter(row => !previous.some(old => old.id === row.id))] : list.actions
      )
      $actionsNextCursor.set(list.next_cursor)
      $actionsError.set(null)
    } catch (error) {
      if (scope?.() && epoch === currentEpoch && revision === readRevision) {
        $actionsError.set(String(error))
      }
    }
  })().finally(() => {
    if (refreshRequest === request) {
      refreshRequest = null
    }

    if (scope?.() && epoch === currentEpoch && revision === readRevision) {
      $actionsLoading.set(false)
    }
  })

  refreshRequest = request

  return request
}

export async function loadMoreActions(): Promise<void> {
  await refreshActions(activeQuery, true)
}

export async function createAction(input: ActionDefinitionRequest): Promise<ActionDefinition> {
  const action = await actionsApi.create(input)
  await refreshActions()

  return action
}

export async function updateAction(actionId: string, input: ActionUpdateRequest): Promise<ActionDefinition> {
  const action = await actionsApi.update(actionId, input)
  await refreshActions()

  return action
}

export async function deleteAction(actionId: string): Promise<void> {
  await actionsApi.remove(actionId)
  await refreshActions()
}

export function resetActionsForAccount(): void {
  epoch += 1
  readRevision += 1
  activeQuery = ''
  $actionsNextCursor.set(null)
  refreshRequest = null
  $actions.set([])
  $actionsLoading.set(false)
  $actionsError.set(null)
}

registerStorageClearHandler(resetActionsForAccount)
