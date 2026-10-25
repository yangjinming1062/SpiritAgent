import { defineAccountUiAtom } from '@/shared/lib/storage'
import { isLifeUiSnapshot, type LifeUiSnapshot } from '@ipc/contracts'

const state = defineAccountUiAtom<LifeUiSnapshot>({
  key: 'da.life.ui',
  fallback: { status: 'active', pursuitPage: 0, pursuitCursors: [null], eventPage: 0, selectedId: null, scrollTop: 0 },
  isPersistable: isLifeUiSnapshot
})

export const $lifeUi = state.$atom
export const $lifeUiRestoreRevision = state.$importRevision
export const updateLifeUi = state.set

export function selectLifePursuit(selectedId: string | null): void {
  state.set({ selectedId, eventPage: 0, scrollTop: 0 })
  $lifeUiRestoreRevision.set($lifeUiRestoreRevision.get() + 1)
}

export function exportLifeUiSnapshot(): LifeUiSnapshot {
  return structuredClone($lifeUi.get())
}

export function importLifeUiSnapshot(snapshot: LifeUiSnapshot): void {
  if (isLifeUiSnapshot(snapshot)) {
    state.set(structuredClone(snapshot))
    $lifeUiRestoreRevision.set($lifeUiRestoreRevision.get() + 1)
  }
}
