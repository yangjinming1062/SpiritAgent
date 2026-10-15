import { atom } from 'nanostores'

import { isRecord } from '@/shared/lib/is-record'
import { accountStorageKey, definePersistedAtom, storedJson } from '@/shared/lib/storage'

interface AppearanceUiState {
  selectedOutfitId: number | null
  designing: boolean
  text: string
  draftId: number | null
}

const FALLBACK: AppearanceUiState = { selectedOutfitId: null, designing: false, text: '', draftId: null }
const KEY = 'da.companion.wardrobe.ui.v1'

function validState(value: unknown): value is AppearanceUiState {
  return (
    isRecord(value) &&
    typeof value.designing === 'boolean' &&
    typeof value.text === 'string' &&
    [value.selectedOutfitId, value.draftId].every(
      id => id === null || (typeof id === 'number' && Number.isSafeInteger(id) && id > 0)
    )
  )
}

const state = definePersistedAtom<AppearanceUiState>({ key: KEY, fallback: FALLBACK, isPersistable: validState })
export const $appearanceUi = state.$atom
export const updateAppearanceUi = state.set
export const $appearanceUiImportRevision = atom(0)

window.addEventListener('storage', event => {
  if (event.key === accountStorageKey(KEY)) {
    state.$atom.set(storedJson(KEY, FALLBACK, validState))
    $appearanceUiImportRevision.set($appearanceUiImportRevision.get() + 1)
  }
})
