import { atom } from 'nanostores'

import { shellApi } from '@/shared/platform/desktop'
import type { PresentationState } from '@ipc/contracts'

import { log } from '../lib/log'

export const $presentation = atom<PresentationState>({
  requestedMode: 'window',
  effectiveMode: 'window',
  prepareDesktopMedia: false,
  status: 'inactive',
  failureReason: null,
  supported: false,
  foreground: false,
  stageAvailable: false,
  fullscreen: false,
  compatibilityWarning: null,
  companionActivity: 'idle',
  voicePreparing: false,
  displayId: null,
  workArea: null,
  displays: [],
  stageOwner: 'sprite',
  stageVisible: true,
  stageEpoch: 0,
  revision: -1,
  switchBlockedReason: null
})

export function hydratePresentation(): () => void {
  let disposed = false

  const apply = (state: PresentationState): void => {
    if (!disposed && state.revision >= $presentation.get().revision) {
      $presentation.set(state)
    }
  }

  const stop = shellApi().presentation.onChanged(apply)
  void shellApi()
    .presentation.getState()
    .then(apply)
    .catch(error => log.warn('presentation', error))

  return () => {
    disposed = true
    stop()
  }
}
