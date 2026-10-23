import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'

/** 窗口模式的静态造型来源：当前影像冻结的造型路径与描述，不读取“当前穿着”。 */
export interface StageOutfitSource {
  path: string
  name: string
}

export const $stageOutfit = atom<{ url: string; name: string; path: string } | null>(null)
export const $stageOutfitPresented = atom<string | null>(null)
/** 尚未取得呈现事实为 preparing；有冻结素材但解析不到地址为 error；影像没有冻结造型为 missing。 */
export const $stageOutfitStatus = atom<'preparing' | 'ready' | 'missing' | 'error'>('preparing')

let source: StageOutfitSource | null = null
let generation = 0

export function setStageOutfitSource(next: StageOutfitSource | null): void {
  if (source?.path === next?.path && source?.name === next?.name) {
    return
  }

  source = next

  if (!next) {
    generation += 1
    $stageOutfit.set(null)
    $stageOutfitStatus.set('missing')

    return
  }

  void resolveFrozenOutfit(false)
}

/** 取图失败后的用户重试：按不可变路径重新解析，不换素材身份。 */
export function retryStageOutfit(): void {
  if (source) {
    void resolveFrozenOutfit(true)
  }
}

async function resolveFrozenOutfit(revalidate: boolean): Promise<void> {
  const wanted = source

  if (!wanted) {
    return
  }

  const token = ++generation
  const scope = captureAuthScope()

  $stageOutfitStatus.set('preparing')

  if (!scope) {
    return
  }

  try {
    const url = await backendApi().apiAsset({ url: wanted.path, preferCache: !revalidate })

    if (!scope() || token !== generation || source !== wanted) {
      return
    }

    if (url) {
      $stageOutfit.set({ url, name: wanted.name, path: wanted.path })
    }

    $stageOutfitStatus.set(url ? 'ready' : 'error')
  } catch (error) {
    if (scope() && token === generation) {
      $stageOutfitStatus.set('error')
      log.warn('stage-outfit', 'Frozen outfit asset unavailable', error)
    }
  }
}

registerStorageClearHandler(() => {
  source = null
  generation += 1
  $stageOutfit.set(null)
  $stageOutfitPresented.set(null)
  $stageOutfitStatus.set('preparing')
})
