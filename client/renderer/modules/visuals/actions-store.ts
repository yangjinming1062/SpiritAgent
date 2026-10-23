import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import { actionsApi } from './api'
import type { ActionDefinition, ActionDefinitionRequest, ActionUpdateRequest } from './types'

export const $actions = atom<ActionDefinition[]>([])
export const $actionsLoading = atom(false)
export const $actionsError = atom<string | null>(null)

let epoch = 0
let refreshRequest: Promise<void> | null = null

export async function refreshActions(): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  if (refreshRequest) {
    return refreshRequest
  }

  const currentEpoch = epoch
  $actionsLoading.set(true)

  const request = (async (): Promise<void> => {
    try {
      const list = await actionsApi.list()

      if (!scope?.() || epoch !== currentEpoch) {
        return
      }

      $actions.set(list.actions)
      $actionsError.set(null)
    } catch (error) {
      if (scope?.() && epoch === currentEpoch) {
        $actionsError.set(String(error))
      }
    }
  })().finally(() => {
    if (refreshRequest === request) {
      refreshRequest = null
    }

    if (scope?.() && epoch === currentEpoch) {
      $actionsLoading.set(false)
    }
  })

  refreshRequest = request

  return request
}

export async function createAction(input: ActionDefinitionRequest): Promise<ActionDefinition> {
  const action = await actionsApi.create(input)
  await refreshActions()

  return action
}

export async function updateAction(actionId: number, input: ActionUpdateRequest): Promise<ActionDefinition> {
  const action = await actionsApi.update(actionId, input)
  await refreshActions()

  return action
}

export async function deleteAction(actionId: number): Promise<void> {
  await actionsApi.remove(actionId)
  await refreshActions()
}

export function resetActionsForAccount(): void {
  epoch += 1
  refreshRequest = null
  $actions.set([])
  $actionsLoading.set(false)
  $actionsError.set(null)
}

registerStorageClearHandler(resetActionsForAccount)
