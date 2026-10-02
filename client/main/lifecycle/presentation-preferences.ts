import path from 'node:path'

import type { PresentationMode } from '@ipc/contracts'

import { atomicWriteFile, createSerialQueue, safeReadJson } from '../shared/utils'

interface PresentationPreferences {
  version: 1
  mode: PresentationMode
  displayId: number | null
}

export function createPresentationPreferences(userData: string) {
  const filename = path.join(userData, 'desktop-presentation.json')
  const raw = safeReadJson<Partial<PresentationPreferences>>(filename)

  let state: PresentationPreferences = {
    version: 1,
    mode: raw?.version === 1 && raw.mode === 'desktop' ? 'desktop' : 'window',
    displayId: raw?.version === 1 && Number.isSafeInteger(raw.displayId) ? (raw.displayId ?? null) : null
  }

  const serial = createSerialQueue()

  return {
    get: (): PresentationPreferences => ({ ...state }),
    set: (patch: Partial<Pick<PresentationPreferences, 'mode' | 'displayId'>>): Promise<void> =>
      serial(async () => {
        const next = { ...state, ...patch }
        await atomicWriteFile(filename, JSON.stringify(next))
        state = next
      })
  }
}
