import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { type DiaryUiSnapshot, isDiaryUiSnapshot } from '@ipc/contracts'

import { emptyArchiveSearch } from '../../../../../../shared/archive-search'

function initialState(): DiaryUiSnapshot {
  const now = new Date()
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`

  return {
    search: emptyArchiveSearch(),
    selectedDate: `${month}-${String(now.getDate()).padStart(2, '0')}`,
    month: `${month}-01`,
    scrollTop: 0
  }
}

export const $diaryUi = atom<DiaryUiSnapshot>(initialState())
export const updateDiaryUi = (patch: Partial<DiaryUiSnapshot>): void => $diaryUi.set({ ...$diaryUi.get(), ...patch })

export function exportDiaryUiSnapshot(): DiaryUiSnapshot {
  return structuredClone($diaryUi.get())
}

export function importDiaryUiSnapshot(snapshot: DiaryUiSnapshot): void {
  if (isDiaryUiSnapshot(snapshot)) {
    $diaryUi.set(structuredClone(snapshot))
  }
}

registerStorageClearHandler(() => $diaryUi.set(initialState()))
