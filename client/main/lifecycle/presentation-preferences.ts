import path from 'node:path'

import type { PresentationMode } from '@ipc/contracts'

import { atomicWriteFile, createSerialQueue, safeReadJson } from '../shared/utils'

interface PresentationPreferences {
  version: 2
  mode: PresentationMode
  displayId: number | null
  companionAlwaysOnTop: boolean
}

export function createPresentationPreferences(userData: string) {
  const filename = path.join(userData, 'desktop-presentation.json')
  const raw = safeReadJson<Partial<Omit<PresentationPreferences, 'version'>> & { version?: number }>(filename)
  const supported = raw?.version === 1 || raw?.version === 2

  let state: PresentationPreferences = {
    version: 2,
    mode: supported && raw.mode === 'desktop' ? 'desktop' : 'window',
    displayId: supported && Number.isSafeInteger(raw.displayId) ? (raw.displayId ?? null) : null,
    companionAlwaysOnTop: supported && raw.companionAlwaysOnTop === true
  }

  const serial = createSerialQueue()

  return {
    get: (): PresentationPreferences => ({ ...state }),
    set: (
      patch: Partial<Pick<PresentationPreferences, 'mode' | 'displayId' | 'companionAlwaysOnTop'>>
    ): Promise<void> =>
      serial(async () => {
        const next = { ...state, ...patch }
        await atomicWriteFile(filename, JSON.stringify(next))
        state = next
      })
  }
}
