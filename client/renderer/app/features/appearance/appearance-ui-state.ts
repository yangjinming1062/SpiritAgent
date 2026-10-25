import { defineAccountUiAtom } from '@/shared/lib/storage'
import { isRecord } from '@client-shared/is-record'

import { isResourceId } from '../../../../../shared/resource-id'

interface AppearanceUiState {
  selectedOutfitId: string | null
  designing: boolean
  text: string
  draftId: string | null
  listScrollTop?: number
}

const FALLBACK: AppearanceUiState = { selectedOutfitId: null, designing: false, text: '', draftId: null }
const KEY = 'da.companion.wardrobe.ui.v1'

function validState(value: unknown): value is AppearanceUiState {
  return (
    isRecord(value) &&
    typeof value.designing === 'boolean' &&
    typeof value.text === 'string' &&
    (value.listScrollTop === undefined ||
      (typeof value.listScrollTop === 'number' && Number.isFinite(value.listScrollTop) && value.listScrollTop >= 0)) &&
    [value.selectedOutfitId, value.draftId].every(id => id === null || isResourceId(id))
  )
}

const state = defineAccountUiAtom<AppearanceUiState>({ key: KEY, fallback: FALLBACK, isPersistable: validState })
export const $appearanceUi = state.$atom
export const updateAppearanceUi = state.set
export const $appearanceUiImportRevision = state.$importRevision
