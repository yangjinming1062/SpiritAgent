import { atom, computed } from 'nanostores'

// 本地准备状态以引用计数管理，与主进程镜像的状态合并。

const $localVoicePreparing = atom(false)
const $desktopVoicePreparing = atom(false)
export const $voicePreparing = computed(
  [$localVoicePreparing, $desktopVoicePreparing],
  (local, desktop) => local || desktop
)

export function setDesktopVoicePreparing(preparing: boolean): void {
  $desktopVoicePreparing.set(preparing)
}

let activeCount = 0

export function beginVoicePreparing(): void {
  activeCount++

  if (activeCount === 1) {
    $localVoicePreparing.set(true)
  }
}

export function endVoicePreparing(): void {
  if (activeCount === 0) {
    return
  }

  activeCount--

  if (activeCount === 0) {
    $localVoicePreparing.set(false)
  }
}
