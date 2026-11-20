import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { requestResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import type { ActionDefinition, ActionLibraryGroup } from '@protocol'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { creationSubmitter } from '../creation-request'

import { refreshActionActivity } from './activity-store'

const submitCreation = creationSubmitter('da.actions.createRequests')

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
  const sameQuery = search === state.query
  const previousCursor = state.nextCursor.get()

  if (append && (state.loading.get() || search !== state.query)) {
    return
  }

  const cursor = append ? state.nextCursor.get() : null

  if (append && !cursor) {
    return
  }

  if (!append) {
    // 刷新建立新快照，动作在分组间移动后不能继续使用旧分页游标。
    state.nextCursor.set(null)
  }

  if (!sameQuery) {
    state.rows.set([])
    state.nextCursor.set(null)
    state.loaded.set(false)
  }

  state.query = search
  const revision = ++state.revision
  const currentEpoch = epoch
  state.loading.set(true)

  try {
    const list = await requestResource('companion_actions.list_actions', {
      query: { group, query: search || undefined, cursor: cursor ?? undefined, limit: 50 }
    })

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
      if (!append && sameQuery) {
        state.nextCursor.set(previousCursor)
      }

      state.error.set(String(error))
    }
  } finally {
    if (scope() && currentEpoch === epoch && revision === state.revision) {
      state.loading.set(false)
      state.loaded.set(true)
    }
  }
}

/** 动作目录变化事件只作刷新触发。 */
export function onActionEvent(): void {
  void refreshActionGroups()
  void refreshActionActivity()
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

/** 整理一句描述：只产出草稿，不登记、不评审；客户端不解析 draft 结构。 */
export async function interpretAction(
  description: string,
  actionId?: string
): Promise<ApiSchemas['ActionInterpretation']> {
  return requestResource('companion_actions.interpret_action', { body: { description, action_id: actionId ?? null } })
}

/** 用户确认后才登记；同一份设计复用请求身份，重试不会产生第二个动作。 */
export async function createAction(design: Record<string, unknown>): Promise<ActionDefinition> {
  const scope = captureAuthScope()

  const action = await submitCreation(design, request_id =>
    requestResource('companion_actions.create_action', { body: { request_id, design } })
  )

  if (scope?.()) {
    await refreshActionGroups()
  }

  return action
}

export async function updateAction(
  actionId: string,
  design: Record<string, unknown>,
  expectedUpdatedAt: string
): Promise<ActionDefinition> {
  const scope = captureAuthScope()

  const action = await requestResource('companion_actions.update_action', {
    path: { action_id: actionId },
    body: { design, expected_updated_at: expectedUpdatedAt }
  })

  if (scope?.()) {
    await refreshActionGroups()
  }

  return action
}

export async function retryActionReview(action: ActionDefinition): Promise<void> {
  const scope = captureAuthScope()
  await requestResource('companion_actions.retry_review', {
    path: { action_id: action.id },
    body: { expected_updated_at: action.updated_at }
  })

  if (scope?.()) {
    await refreshActionGroups()
  }
}

export async function deleteAction(actionId: string): Promise<void> {
  const scope = captureAuthScope()
  await requestResource('companion_actions.delete_action', { path: { action_id: actionId } })

  if (scope?.()) {
    await refreshActionGroups()
  }
}

function resetActionsForAccount(): void {
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
