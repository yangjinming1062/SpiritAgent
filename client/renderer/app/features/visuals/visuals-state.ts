import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { isVisualsUiSnapshot, type VisualsUiSnapshot } from '@ipc/contracts'

const FALLBACK: VisualsUiSnapshot = {
  query: '',
  mediaKind: 'all',
  source: '',
  page: 0,
  cursors: [null]
}

// 影像筛选与制作草稿是会话级状态：跨切页保留、换号清空，不落盘；详情随组件生命周期。
const ui = atom<VisualsUiSnapshot>(FALLBACK)

export const $visualsUi = ui

export function updateVisualsUi(patch: Partial<VisualsUiSnapshot>): void {
  const next = patch.page === 0 ? { ...patch, cursors: [null] } : patch
  ui.set({ ...ui.get(), ...next })
}

export function exportVisualsUiSnapshot(): VisualsUiSnapshot {
  return structuredClone(ui.get())
}

export function importVisualsUiSnapshot(snapshot: VisualsUiSnapshot): void {
  if (isVisualsUiSnapshot(snapshot)) {
    ui.set(structuredClone(snapshot))
  }
}

registerStorageClearHandler(() => {
  ui.set(FALLBACK)
})
