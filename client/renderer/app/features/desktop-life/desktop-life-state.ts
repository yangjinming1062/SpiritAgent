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

type DesktopLifeCreate = DesktopLifeUiSnapshot['create']

/** 合并创建草稿子树：一次写入，页面不必读出整棵子树再构造新对象写回。 */
export function updateDesktopLifeCreate(
  partial: Partial<DesktopLifeCreate> | ((current: DesktopLifeCreate) => Partial<DesktopLifeCreate>)
): void {
  const current = ui.get().create
  const next = typeof partial === 'function' ? partial(current) : partial

  ui.set({ create: { ...current, ...next } })
}

export function exportDesktopLifeUiSnapshot(): DesktopLifeUiSnapshot {
  return structuredClone(ui.get())
}

export function importDesktopLifeUiSnapshot(snapshot: DesktopLifeUiSnapshot): void {
  if (isDesktopLifeUiSnapshot(snapshot)) {
    ui.set(structuredClone(snapshot))
  }
}
