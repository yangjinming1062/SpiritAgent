import type { PresentationState } from '@ipc/contracts'
import { atom } from 'nanostores'

import { log } from '../lib/log'

export const $presentation = atom<PresentationState>({
  requestedMode: 'window',
  effectiveMode: 'window',
  status: 'inactive',
  failureReason: null,
  supported: false,
  foreground: false,
  stageAvailable: false,
  fullscreen: false,
  companionAlwaysOnTop: false,
  stageInsets: { top: 48, bottom: 96, left: 16, right: 16 },
  compatibilityWarning: null,
  companionActivity: 'idle',
  voicePreparing: false,
  displayId: null,
  displays: [],
  wallpaperTarget: null,
  stageOwner: 'sprite',
  stageVisible: true,
  stageEpoch: 0,
  revision: -1
})

export function hydratePresentation(): () => void {
  let disposed = false

  const apply = (state: PresentationState): void => {
    if (!disposed && state.revision >= $presentation.get().revision) {
      $presentation.set(state)
    }
  }

  const stop = window.spiritagent.presentation.onChanged(apply)
  void window.spiritagent.presentation
    .getState()
    .then(apply)
    .catch(error => log.warn('presentation', error))

  return () => {
    disposed = true
    stop()
  }
}
