import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'
import { currentClearEpoch } from '@/shared/lib/storage'
import type { DesktopRunnerPhase } from '@ipc/contracts'

// Runner 状态镜像；先订阅再读取快照，迟到快照不能覆盖更新的状态事件。
export const $runnerPhase = atom<DesktopRunnerPhase>('idle')

let offRunnerStatus: (() => void) | null = null
let statusRevision = 0
let hydration: { epoch: number; request: Promise<void> } | undefined

export function hydrateRunnerStatus(): Promise<void> {
  const desktop = window.spiritagent
  const epoch = currentClearEpoch()

  if (!offRunnerStatus) {
    offRunnerStatus =
      desktop.onRunnerStatus?.(ev => {
        statusRevision++
        $runnerPhase.set(
          ev.type === 'running' || ev.type === 'runner_ready'
            ? 'running'
            : ev.type === 'stopping'
              ? 'stopping'
              : 'stopped'
        )
      }) ?? null
  }

  if (hydration?.epoch === epoch) {
    return hydration.request
  }

  const revision = statusRevision

  const request = Promise.resolve()
    .then(() => desktop.runnerGetState?.())
    .then(state => {
      if (state?.phase && revision === statusRevision && epoch === currentClearEpoch()) {
        $runnerPhase.set(state.phase)
      }
    })
    .catch(error => log.warn('runner-status', 'runnerGetState failed', error))
    .finally(() => {
      if (hydration?.request === request) {
        hydration = undefined
      }
    })

  hydration = { epoch, request }

  return request
}
