import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

// 环境草稿是会话级状态：跨切页保留、换号清空，不落盘；库/创建/详情视图随组件生命周期。
export type ScenePageView = { kind: 'create' } | { kind: 'detail'; sceneId: string } | { kind: 'library' }

export interface SceneUiState {
  notes: string
  editDrafts: Record<string, { title: string; description: string }>
}

const FALLBACK: SceneUiState = { notes: '', editDrafts: {} }

const state = atom<SceneUiState>(FALLBACK)

export const $sceneUi = state
export const updateSceneUi = (patch: Partial<SceneUiState>): void => state.set({ ...state.get(), ...patch })

registerStorageClearHandler(() => {
  state.set(FALLBACK)
})
