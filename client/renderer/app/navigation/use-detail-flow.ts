import { useCallback, useLayoutEffect, useRef } from 'react'

import { type NavigationDetailSelector, useNavigationDetail } from './navigation-detail'

/**
 * 列表→详情流的统一装配：导航瞬态详情消费 + 列表滚动位置保存/恢复。
 *
 * 详情打开时滚动容器归零；返回列表时恢复离开前的位置。
 * 滚动容器由调用方经 `scrollRef` 提供；`readDetail` 仅在导航指向本页时消费，
 * 与 useNavigationDetail 的「离开本页的导航不回退」语义一致。
 */
export function useDetailFlow<T>(options: {
  select: NavigationDetailSelector<T>
  fallback: T
  /** 详情是否处于打开状态（用于滚动恢复方向判断）。 */
  isDetailOpen: (value: T) => boolean
}) {
  const { select, fallback, isDetailOpen } = options
  const [detail, setDetail] = useNavigationDetail(select, fallback)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const listScrollTop = useRef(0)
  const open = isDetailOpen(detail)

  // 详情切换在绘制前恢复滚动：进入详情归零，返回列表恢复保存位置。
  useLayoutEffect(() => {
    const element = scrollRef.current

    if (!element) {
      return
    }

    element.scrollTop = open ? 0 : listScrollTop.current
  }, [open])

  const rememberListScroll = useCallback((): void => {
    listScrollTop.current = scrollRef.current?.scrollTop ?? 0
  }, [])

  /** 打开详情前记住当前列表位置；返回列表用 close 恢复。 */
  const openDetail = useCallback(
    (next: T): void => {
      rememberListScroll()
      setDetail(next)
    },
    [rememberListScroll, setDetail]
  )

  const closeDetail = useCallback((): void => setDetail(fallback), [fallback, setDetail])

  return { detail, openDetail, closeDetail, setDetail, scrollRef, rememberListScroll, isDetailOpen: open }
}
