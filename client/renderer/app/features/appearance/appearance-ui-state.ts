import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

// 衣柜草稿是会话级状态：跨切页保留、换号清空，不落盘；详情与制作区开关随组件生命周期。
export interface AppearanceUiState {
  text: string
  draftId: string | null
  draftTexts?: Record<string, string>
}

const FALLBACK: AppearanceUiState = { text: '', draftId: null }

const state = atom<AppearanceUiState>(FALLBACK)

export const $appearanceUi = state
export const updateAppearanceUi = (patch: Partial<AppearanceUiState>): void => state.set({ ...state.get(), ...patch })

export function selectAppearanceDraft(id: string | null): void {
  const current = state.get()
  const draftTexts = { ...current.draftTexts, [current.draftId ?? 'new']: current.text }
  state.set({ draftId: id, text: draftTexts[id ?? 'new'] ?? '', draftTexts })
}

registerStorageClearHandler(() => {
  state.set(FALLBACK)
})
