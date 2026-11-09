import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { isSceneUiSnapshot, type SceneUiSnapshot } from '@ipc/contracts'

// 环境草稿是会话级状态：跨切页保留、跨模式交接、换号清空，不落盘；库/创建/详情视图随组件生命周期。
export type ScenePageView = { kind: 'create' } | { kind: 'detail'; sceneId: string } | { kind: 'library' }

interface SceneUiState {
  notes: string
  editDrafts: Record<string, { title: string; description: string }>
  motionDrafts: SceneUiSnapshot['motionDrafts']
}

const FALLBACK: SceneUiState = { notes: '', editDrafts: {}, motionDrafts: {} }

const state = atom<SceneUiState>(FALLBACK)

export const $sceneUi = state
export const updateSceneUi = (patch: Partial<SceneUiState>): void => state.set({ ...state.get(), ...patch })

export function exportSceneUiSnapshot(): SceneUiSnapshot {
  const { notes, editDrafts, motionDrafts } = state.get()

  return { notes, editDrafts: structuredClone(editDrafts), motionDrafts: structuredClone(motionDrafts) }
}

export function importSceneUiSnapshot(snapshot: SceneUiSnapshot): void {
  if (isSceneUiSnapshot(snapshot)) {
    state.set(structuredClone(snapshot))
  }
}

registerStorageClearHandler(() => {
  state.set(FALLBACK)
})
