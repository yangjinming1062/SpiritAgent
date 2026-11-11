import { atom } from 'nanostores'

import { gatewayApi } from '@/shared/platform/desktop'

export const $callAudioBusy = atom(false)

export function bindCallAudioFocus(): () => void {
  let revision = 0

  const dispose = gatewayApi().onVoiceEvent(event => {
    if (event.kind === 'busy') {
      revision++
      $callAudioBusy.set(event.busy)
    }
  })

  void gatewayApi()
    .voiceBusy()
    .then(busy => {
      if (revision === 0) {
        $callAudioBusy.set(busy)
      }
    })
    .catch(() => {})

  return () => {
    revision++
    dispose()
  }
}
