import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { type ActionComposerSnapshot, type ActionsUiSnapshot, isActionsUiSnapshot } from '@ipc/contracts'

import { InterpretationGuard } from '../../../../../shared/interpretation-guard'

const FALLBACK: ActionsUiSnapshot = { composer: null }

// 动作编辑器草稿是会话级状态：跨切页与模式交接保留、换号清空，不落盘。
const ui = atom<ActionsUiSnapshot>(FALLBACK)

const interpretation = new InterpretationGuard()

export const $actionsUi = ui

/** 草稿唯一写入口：任何变化都推进整理修订，在途整理的响应不落地。 */
export function updateActionsComposer(
  next: ActionComposerSnapshot | null | ((current: ActionComposerSnapshot | null) => ActionComposerSnapshot | null)
): void {
  interpretation.revise()
  ui.set({ ...ui.get(), composer: typeof next === 'function' ? next(ui.get().composer) : next })
}

/** 发起一次动作描述整理，返回响应回来时的落地校验。 */
export function beginActionInterpretation(): () => boolean {
  return interpretation.begin()
}

export function exportActionsUiSnapshot(): ActionsUiSnapshot {
  return structuredClone(ui.get())
}

export function importActionsUiSnapshot(snapshot: ActionsUiSnapshot): void {
  if (isActionsUiSnapshot(snapshot)) {
    interpretation.revise()
    ui.set(structuredClone(snapshot))
  }
}

registerStorageClearHandler(() => {
  interpretation.revise()
  ui.set(FALLBACK)
})
