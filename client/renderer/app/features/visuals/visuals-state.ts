import { defineAccountUiAtom } from '@/shared/lib/storage'
import { isVisualsUiSnapshot, type VisualsUiSnapshot } from '@ipc/contracts'

const fallback: VisualsUiSnapshot = {
  query: '',
  mediaKind: 'all',
  source: '',
  page: 0,
  selectedId: null,
  detailOpen: false
}

const ui = defineAccountUiAtom<VisualsUiSnapshot>({
  key: 'da.visuals.ui.v1',
  fallback,
  isPersistable: isVisualsUiSnapshot
})

export const $visualsUi = ui.$atom
export const updateVisualsUi = ui.set

export function exportVisualsUiSnapshot(): VisualsUiSnapshot {
  return structuredClone($visualsUi.get())
}

export function importVisualsUiSnapshot(snapshot: VisualsUiSnapshot): void {
  if (isVisualsUiSnapshot(snapshot)) {
    ui.set(structuredClone(snapshot))
  }
}
