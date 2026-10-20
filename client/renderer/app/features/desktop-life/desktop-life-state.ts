import { definePersistedAtom } from '@/shared/lib/storage'
import { type DesktopLifeUiSnapshot, isDesktopLifeUiSnapshot } from '@ipc/contracts'

export function newDesktopLifeCreation(): DesktopLifeUiSnapshot['create'] {
  return {
    name: '',
    sceneId: '',
    sceneLabel: '',
    outfitId: '',
    source: 'ai',
    feedback: '',
    targetDisplayId: null,
    targetResolution: null,
    requestId: crypto.randomUUID()
  }
}

const fallback: DesktopLifeUiSnapshot = {
  selectedSetId: null,
  selectedActionId: null,
  creating: false,
  create: newDesktopLifeCreation(),
  feedback: {},
  design: { name: '', description: '', kind: 'loop', duration: 10 },
  addActionOpen: false
}

const ui = definePersistedAtom<DesktopLifeUiSnapshot>({
  key: 'da.desktop-life.ui.v2',
  fallback,
  isPersistable: isDesktopLifeUiSnapshot
})

export const $desktopLifeUi = ui.$atom
export const updateDesktopLifeUi = ui.set

export function exportDesktopLifeUiSnapshot(): DesktopLifeUiSnapshot {
  return structuredClone(ui.get())
}

export function importDesktopLifeUiSnapshot(snapshot: DesktopLifeUiSnapshot): void {
  if (isDesktopLifeUiSnapshot(snapshot)) {
    ui.set(structuredClone(snapshot))
  }
}
