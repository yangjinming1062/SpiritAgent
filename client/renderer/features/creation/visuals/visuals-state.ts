import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { isVisualsUiSnapshot, type StudioUiSnapshot, type VisualsUiSnapshot } from '@ipc/contracts'

import { InterpretationGuard } from '../../../../../shared/interpretation-guard'

export const EMPTY_STUDIO: StudioUiSnapshot = {
  mode: 'idle',
  intent: 'photo',
  description: '',
  preview: null,
  refinement: ''
}

const FALLBACK: VisualsUiSnapshot = {
  query: '',
  mediaKind: 'all',
  source: '',
  page: 0,
  cursors: [null],
  studio: { ...EMPTY_STUDIO }
}

// 影像筛选与制作草稿是会话级状态：跨切页保留、换号清空，不落盘；详情随组件生命周期。
const ui = atom<VisualsUiSnapshot>(FALLBACK)

const interpretation = new InterpretationGuard()

export const $visualsUi = ui

/** 筛选与分页变化不影响在途整理；制作草稿变化必须走 updateStudio。 */
export function updateVisualsUi(patch: Partial<Omit<VisualsUiSnapshot, 'studio'>>): void {
  const next = patch.page === 0 ? { ...patch, cursors: [null] } : patch
  ui.set({ ...ui.get(), ...next })
}

/** 制作草稿唯一写入口：任何变化都推进整理修订，在途整理的响应不落地。 */
export function updateStudio(next: Partial<StudioUiSnapshot>): void {
  interpretation.revise()
  const studio = ui.get().studio ?? { ...EMPTY_STUDIO }
  ui.set({ ...ui.get(), studio: { ...studio, ...next } })
}

/** 发起一次影像描述整理，返回响应回来时的落地校验。 */
export function beginStudioInterpretation(): () => boolean {
  return interpretation.begin()
}

export function exportVisualsUiSnapshot(): VisualsUiSnapshot {
  return structuredClone(ui.get())
}

export function importVisualsUiSnapshot(snapshot: VisualsUiSnapshot): void {
  if (isVisualsUiSnapshot(snapshot)) {
    interpretation.revise()
    ui.set(structuredClone(snapshot))
  }
}

registerStorageClearHandler(() => {
  interpretation.revise()
  ui.set(FALLBACK)
})
