import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import { type ActionDefinition, actionsApi } from '../visuals'

/** 动作待处理摘要：只读投影，不驱动列表分页与搜索，任务状态以服务端为准。 */
export interface ActionActivityEntry {
  id: string
  status: ActionDefinition['status']
}

const entries = atom<ActionActivityEntry[]>([])

export const $actionActivity = entries

let epoch = 0

/**
 * 补读未入库动作摘要：登录、重连与目录变化事件触发，不受动作库列表的搜索
 * 与分页影响。单页最多 100 条，最多翻 5 页；超出时以已读内容为准，等待下一
 * 次目录变化事件补齐。
 */
export async function refreshActionActivity(): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  const currentEpoch = epoch
  let cursor: string | undefined
  const collected: ActionActivityEntry[] = []

  for (let page = 0; page < 5; page += 1) {
    const list = await actionsApi.list({ group: 'not_in_library', cursor, limit: 100 })

    if (!scope() || epoch !== currentEpoch) {
      return
    }

    collected.push(...list.actions.map(action => ({ id: action.id, status: action.status })))

    if (!list.has_more || !list.next_cursor) {
      break
    }

    cursor = list.next_cursor
  }

  entries.set(collected)
}

export function resetActionActivity(): void {
  epoch += 1
  entries.set([])
}

registerStorageClearHandler(resetActionActivity)
