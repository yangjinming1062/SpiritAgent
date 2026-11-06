import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { type AppearanceUiSnapshot, isAppearanceUiSnapshot } from '@ipc/contracts'

// 衣柜草稿是会话级状态：跨切页保留、跨模式交接、换号清空，不落盘；详情与制作区开关随组件生命周期。
interface AppearanceUiState {
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

export function exportAppearanceUiSnapshot(): AppearanceUiSnapshot {
  const { text, draftId, draftTexts } = state.get()

  return { text, draftId, draftTexts: { ...draftTexts } }
}

export function importAppearanceUiSnapshot(snapshot: AppearanceUiSnapshot): void {
  if (isAppearanceUiSnapshot(snapshot)) {
    state.set({ text: snapshot.text, draftId: snapshot.draftId, draftTexts: { ...snapshot.draftTexts } })
  }
}

registerStorageClearHandler(() => {
  state.set(FALLBACK)
})
