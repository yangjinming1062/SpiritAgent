import { useStore } from '@nanostores/react'
import type { Dispatch, SetStateAction } from 'react'
import { useLayoutEffect, useRef, useState } from 'react'

import { $appNavigationRevision, $appNavigationTarget } from '@/app/navigation/app-navigation'
import type { AppNavigationTarget } from '@ipc/contracts'

/** 导航指向本页时返回详情值；返回 undefined 表示本次导航与该详情无关，保持现状。 */
export type NavigationDetailSelector<T> = (target: AppNavigationTarget) => T | undefined

/**
 * 消费导航瞬态详情参数（挂载时与每次导航后各一次）。仅当导航指向本页时同步详情；
 * 离开本页的导航不回退状态，避免仍挂载的旧页面在切换瞬间闪现上级列表。
 */
export function useNavigationDetail<T>(
  select: NavigationDetailSelector<T>,
  fallback: T
): [T, Dispatch<SetStateAction<T>>] {
  const target = useStore($appNavigationTarget)
  const revision = useStore($appNavigationRevision)
  const [detail, setDetail] = useState<T>(() => select(target) ?? fallback)
  const consumedRevision = useRef(-1)

  useLayoutEffect(() => {
    if (consumedRevision.current === revision) {
      return
    }

    consumedRevision.current = revision
    const next = select(target)

    if (next !== undefined) {
      setDetail(next)
    }
  }, [revision, select, target])

  return [detail, setDetail]
}
