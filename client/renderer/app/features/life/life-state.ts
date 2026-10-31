import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { isLifeUiSnapshot, type LifeUiSnapshot } from '@ipc/contracts'

const FALLBACK: LifeUiSnapshot = {
  status: 'active',
  pursuitPage: 0,
  pursuitCursors: [null],
  eventPage: 0,
  scrollTop: 0
}

// 生活页筛选与阅读进度是会话级状态：跨切页保留、换号清空，不落盘。
const state = atom<LifeUiSnapshot>(FALLBACK)

/** 快照导入修订号：模式交接后生活页重新锚定滚动位置用。 */
export const $lifeUiRestoreRevision = atom(0)

export const $lifeUi = state
export const updateLifeUi = (patch: Partial<LifeUiSnapshot>): void => state.set({ ...state.get(), ...patch })

export function exportLifeUiSnapshot(): LifeUiSnapshot {
  return structuredClone(state.get())
}

export function importLifeUiSnapshot(snapshot: LifeUiSnapshot): void {
  if (isLifeUiSnapshot(snapshot)) {
    state.set(structuredClone(snapshot))
    $lifeUiRestoreRevision.set($lifeUiRestoreRevision.get() + 1)
  }
}

registerStorageClearHandler(() => {
  state.set(FALLBACK)
})
