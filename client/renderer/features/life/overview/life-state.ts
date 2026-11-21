import { atom } from 'nanostores'
import type { SetStateAction } from 'react'

import { registerStorageClearHandler } from '@/shared/lib/storage'

import {
  initialLifeReader,
  isLifeReaderState,
  type LifeReaderState,
  visitLife
} from '../../../../../shared/life-reader-state'

export const $lifeUi = atom<LifeReaderState>(initialLifeReader())
export const $lifeUiRestoreRevision = atom(0)
let restoredNavigation: number | undefined

export function enterLifeOverview(navigation: number, pursuitId?: string): void {
  const saved = $lifeUi.get()

  const state =
    restoredNavigation === navigation || saved.frames.length === 1
      ? saved
      : { frames: [saved.frames[0]], suspended: [...saved.frames.slice(1), ...(saved.suspended ?? [])] }

  restoredNavigation = undefined
  $lifeUi.set(pursuitId ? visitLife(state, { kind: 'pursuit', id: pursuitId }) : state)
}

export function updateLifeUi(action: SetStateAction<LifeReaderState>): void {
  $lifeUi.set(typeof action === 'function' ? action($lifeUi.get()) : action)
}

export function exportLifeUiSnapshot(): LifeReaderState {
  return structuredClone($lifeUi.get())
}

export function importLifeUiSnapshot(snapshot: LifeReaderState, navigation?: number): void {
  if (isLifeReaderState(snapshot)) {
    restoredNavigation = navigation
    $lifeUi.set(structuredClone(snapshot))
    $lifeUiRestoreRevision.set($lifeUiRestoreRevision.get() + 1)
  }
}

registerStorageClearHandler(() => {
  restoredNavigation = undefined
  $lifeUi.set(initialLifeReader())
})
