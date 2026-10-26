import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import { actionsApi } from './api'
import type { ActionDefinition, ActionDefinitionRequest, ActionLibraryGroup, ActionUpdateRequest } from './types'

function createGroupState() {
  return {
    rows: atom<ActionDefinition[]>([]),
    loading: atom(false),
    loaded: atom(false),
    error: atom<string | null>(null),
    nextCursor: atom<string | null>(null),
    query: '',
    revision: 0
  }
}

export const actionGroups = {
  in_library: createGroupState(),
  not_in_library: createGroupState()
}

// 影像页的素材统计始终消费已入库动作。
export const $actions = actionGroups.in_library.rows
let epoch = 0

export async function refreshActions(
  query?: string,
  append = false,
  group: ActionLibraryGroup = 'in_library'
): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  const state = actionGroups[group]
  const search = query ?? state.query

  if (append && (state.loading.get() || search !== state.query)) {
    return
  }

  const cursor = append ? state.nextCursor.get() : null

  if (append && !cursor) {
    return
  }

  if (search !== state.query) {
    state.rows.set([])
    state.nextCursor.set(null)
    state.loaded.set(false)
  }

  state.query = search
  const revision = ++state.revision
  const currentEpoch = epoch
  state.loading.set(true)

  try {
    const list = await actionsApi.list({ group, query: search || undefined, cursor: cursor ?? undefined, limit: 50 })

    if (!scope() || currentEpoch !== epoch || revision !== state.revision) {
      return
    }

    const previous = state.rows.get()
    state.rows.set(
      append ? [...previous, ...list.actions.filter(row => !previous.some(old => old.id === row.id))] : list.actions
    )
    state.nextCursor.set(list.next_cursor)
    state.error.set(null)
  } catch (error) {
    if (scope() && currentEpoch === epoch && revision === state.revision) {
      state.error.set(String(error))
    }
  } finally {
    if (scope() && currentEpoch === epoch && revision === state.revision) {
      state.loading.set(false)
      state.loaded.set(true)
    }
  }
}

export async function refreshActionGroups(): Promise<void> {
  await Promise.all([
    refreshActions(undefined, false, 'in_library'),
    refreshActions(undefined, false, 'not_in_library')
  ])
}

export async function loadMoreActions(group: ActionLibraryGroup): Promise<void> {
  await refreshActions(undefined, true, group)
}

export async function createAction(input: ActionDefinitionRequest): Promise<ActionDefinition> {
  const scope = captureAuthScope()
  const action = await actionsApi.create(input)

  if (scope?.()) {
    await refreshActionGroups()
  }

  return action
}

export async function updateAction(actionId: string, input: ActionUpdateRequest): Promise<ActionDefinition> {
  const scope = captureAuthScope()
  const action = await actionsApi.update(actionId, input)

  if (scope?.()) {
    await refreshActionGroups()
  }

  return action
}

export async function retryActionReview(action: ActionDefinition): Promise<void> {
  const scope = captureAuthScope()
  await actionsApi.review(action.id, action.updated_at)

  if (scope?.()) {
    await refreshActionGroups()
  }
}

export async function deleteAction(actionId: string): Promise<void> {
  const scope = captureAuthScope()
  await actionsApi.remove(actionId)

  if (scope?.()) {
    await refreshActionGroups()
  }
}

export function resetActionsForAccount(): void {
  epoch += 1

  for (const state of Object.values(actionGroups)) {
    state.revision += 1
    state.query = ''
    state.nextCursor.set(null)
    state.rows.set([])
    state.loading.set(false)
    state.loaded.set(false)
    state.error.set(null)
  }
}

registerStorageClearHandler(resetActionsForAccount)
