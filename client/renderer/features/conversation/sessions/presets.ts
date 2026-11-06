import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'

import { $systemPresets, $systemPresetsFetched, $systemPresetsLoading, sessionTokens } from './state'

let presetsLoad: { epoch: number; promise: Promise<void> } | null = null

/** 拉取系统预设元数据；同网关共用在途请求，已拉取过则跳过。 */
export function fetchSystemPresets(): Promise<void> {
  const authCurrent = captureAuthScope()
  const epoch = currentClearEpoch()

  if (!authCurrent || $systemPresetsFetched.get()) {
    return Promise.resolve()
  }

  if (presetsLoad?.epoch === epoch) {
    return presetsLoad.promise
  }

  const token = ++sessionTokens.presets
  const isCurrent = (): boolean => token === sessionTokens.presets && authCurrent()
  $systemPresetsLoading.set(true)

  const load = Promise.resolve()
    .then(() => requestResource('resource_catalog.get_presets'))
    .then(res => {
      if (isCurrent()) {
        $systemPresets.set(res.presets || [])
        $systemPresetsFetched.set(true)
      }
    })
    .catch(err => {
      if (isCurrent()) {
        log.error('session-list', 'Failed to fetch system presets:', err)
      }
    })
    .finally(() => {
      if (token === sessionTokens.presets) {
        $systemPresetsLoading.set(false)
      }

      if (presetsLoad?.promise === load) {
        presetsLoad = null
      }
    })

  presetsLoad = { epoch, promise: load }

  return load
}

registerStorageClearHandler(() => {
  presetsLoad = null
})
