import { defineAccountUiAtom } from '@/shared/lib/storage'
import { isRecord } from '@client-shared/is-record'

import { isResourceId } from '../../../../../shared/resource-id'

interface AppearanceUiState {
  selectedOutfitId: string | null
  designing: boolean
  text: string
  draftId: string | null
  draftTexts?: Record<string, string>
  listScrollTop?: number
}

const FALLBACK: AppearanceUiState = { selectedOutfitId: null, designing: false, text: '', draftId: null }
const KEY = 'da.companion.wardrobe.ui.v1'

function validState(value: unknown): value is AppearanceUiState {
  return (
    isRecord(value) &&
    typeof value.designing === 'boolean' &&
    typeof value.text === 'string' &&
    (value.draftTexts === undefined ||
      (isRecord(value.draftTexts) &&
        Object.entries(value.draftTexts).every(
          ([id, text]) => (id === 'new' || isResourceId(id)) && typeof text === 'string'
        ))) &&
    (value.listScrollTop === undefined ||
      (typeof value.listScrollTop === 'number' && Number.isFinite(value.listScrollTop) && value.listScrollTop >= 0)) &&
    [value.selectedOutfitId, value.draftId].every(id => id === null || isResourceId(id))
  )
}

const state = defineAccountUiAtom<AppearanceUiState>({ key: KEY, fallback: FALLBACK, isPersistable: validState })
export const $appearanceUi = state.$atom
export const updateAppearanceUi = state.set
export const $appearanceUiImportRevision = state.$importRevision

export function selectAppearanceDraft(id: string | null): void {
  const current = state.get()
  const draftTexts = { ...current.draftTexts, [current.draftId ?? 'new']: current.text }
  state.set({ draftId: id, designing: true, text: draftTexts[id ?? 'new'] ?? '', draftTexts })
}
