import { isRecord } from '@/shared/lib/is-record'
import { definePersistedAtom } from '@/shared/lib/storage'
import type { DesktopLifeUiSnapshot } from '@ipc/contracts'

export function newDesktopLifeCreation(): DesktopLifeUiSnapshot['create'] {
  return {
    name: '',
    sceneId: '',
    sceneLabel: '',
    outfitId: '',
    source: 'ai',
    feedback: '',
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
  addActionOpen: false,
  externalActionId: null,
  externalRequirements: '',
  externalFilePath: null
}

function validSnapshot(value: unknown): value is DesktopLifeUiSnapshot {
  if (!isRecord(value) || !isRecord(value.create) || !isRecord(value.feedback) || !isRecord(value.design)) {
    return false
  }

  const create = value.create
  const design = value.design

  return (
    [value.selectedSetId, value.selectedActionId, value.externalActionId].every(
      id => id === null || (typeof id === 'number' && Number.isSafeInteger(id) && id > 0)
    ) &&
    typeof value.creating === 'boolean' &&
    typeof value.addActionOpen === 'boolean' &&
    typeof value.externalRequirements === 'string' &&
    (value.externalFilePath === null || typeof value.externalFilePath === 'string') &&
    ['name', 'sceneId', 'sceneLabel', 'outfitId', 'feedback', 'requestId'].every(
      key => typeof create[key] === 'string'
    ) &&
    ['ai', 'image_upload', 'video_upload'].includes(String(create.source)) &&
    Object.values(value.feedback).every(text => typeof text === 'string') &&
    typeof design.name === 'string' &&
    typeof design.description === 'string' &&
    (design.kind === 'loop' || design.kind === 'once') &&
    typeof design.duration === 'number' &&
    Number.isFinite(design.duration)
  )
}

const ui = definePersistedAtom<DesktopLifeUiSnapshot>({
  key: 'da.desktop-life.ui.v2',
  fallback,
  isPersistable: validSnapshot
})

export const $desktopLifeUi = ui.$atom
export const updateDesktopLifeUi = ui.set

export function exportDesktopLifeUiSnapshot(): DesktopLifeUiSnapshot {
  return structuredClone(ui.get())
}

export function importDesktopLifeUiSnapshot(snapshot: DesktopLifeUiSnapshot): void {
  if (validSnapshot(snapshot)) {
    ui.set(structuredClone(snapshot))
  }
}
