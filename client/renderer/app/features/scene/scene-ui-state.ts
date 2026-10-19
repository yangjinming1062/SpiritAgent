import { atom } from 'nanostores'

import { isRecord } from '@/shared/lib/is-record'
import { accountStorageKey, definePersistedAtom } from '@/shared/lib/storage'

export type ScenePageView = { kind: 'create' } | { kind: 'detail'; sceneId: string } | { kind: 'library' }

interface SceneUiState {
  view: ScenePageView
  notes: string
  editDrafts: Record<string, { title: string; description: string }>
  listScrollTop?: number
}

const FALLBACK: SceneUiState = { view: { kind: 'library' }, notes: '', editDrafts: {} }
const KEY = 'da.companion.background.ui.v1'

function validState(value: unknown): value is SceneUiState {
  if (!isRecord(value) || !isRecord(value.view) || typeof value.notes !== 'string' || !isRecord(value.editDrafts)) {
    return false
  }

  return (
    (value.listScrollTop === undefined ||
      (typeof value.listScrollTop === 'number' && Number.isFinite(value.listScrollTop) && value.listScrollTop >= 0)) &&
    (value.view.kind === 'library' ||
      value.view.kind === 'create' ||
      (value.view.kind === 'detail' && typeof value.view.sceneId === 'string')) &&
    Object.values(value.editDrafts).every(
      draft => isRecord(draft) && typeof draft.title === 'string' && typeof draft.description === 'string'
    )
  )
}

const state = definePersistedAtom<SceneUiState>({ key: KEY, fallback: FALLBACK, isPersistable: validState })
export const $sceneUi = state.$atom
export const updateSceneUi = state.set
export const $sceneUiImportRevision = atom(0)

window.addEventListener('storage', event => {
  if (event.key === accountStorageKey(KEY)) {
    state.reload()
    $sceneUiImportRevision.set($sceneUiImportRevision.get() + 1)
  }
})
