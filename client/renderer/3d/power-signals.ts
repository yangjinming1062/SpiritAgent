import { $focusContext, $screenLocked, $spriteState } from '@/companion'

import { $modelLoadSettled } from './model-store'
import { type PowerProfile, type PowerSignals, resolvePowerProfile } from './PowerProfile'

// 把渲染器已有的原子信号桥接到 3D 引擎的功率档位切换。
// 纯解析逻辑在 PowerProfile.ts；本模块只负责订阅，让组件不必接触信号管线。

function currentSignals(): PowerSignals {
  return {
    spriteState: $spriteState.get(),
    screenLocked: $screenLocked.get(),
    documentHidden: document.visibilityState !== 'visible',
    fullscreen: $focusContext.get()?.fullscreen ?? false,
    staticCovered: false,
    modelSettled: $modelLoadSettled.get()
  }
}

export function subscribePowerProfile(apply: (profile: PowerProfile) => void): () => void {
  let last: PowerProfile | null = null

  const push = (): void => {
    const next = resolvePowerProfile(currentSignals())

    if (next !== last) {
      last = next
      apply(next)
    }
  }

  const unsubs = [
    $spriteState.listen(push),
    $screenLocked.listen(push),
    $focusContext.listen(push),
    $modelLoadSettled.listen(push)
  ]

  const onVisibility = (): void => push()
  document.addEventListener('visibilitychange', onVisibility)

  push()

  return () => {
    for (const off of unsubs) {
      off()
    }

    document.removeEventListener('visibilitychange', onVisibility)
  }
}
