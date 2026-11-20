import type { Dispatch, ReactElement, ReactNode, SetStateAction } from 'react'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

const PageState = createContext<{ prefix: string; values: Map<string, unknown> } | null>(null)

function usePageStore(): { prefix: string; values: Map<string, unknown> } {
  const state = useContext(PageState)
  if (!state) {
    throw new Error('页面状态缺少应用上下文')
  }

  return state
}

export function PageStateProvider({ children, scope }: { children: ReactNode; scope: string }): ReactElement {
  const [state] = useState(() => ({ prefix: `remote:${scope}:ui:`, values: new Map<string, unknown>() }))

  return <PageState.Provider value={state}>{children}</PageState.Provider>
}

export function usePageState<T>(key: string, initial: T, persist = true): [T, Dispatch<SetStateAction<T>>] {
  const state = usePageStore()
  const [value, setValue] = useState<T>(() => {
    if (state.values.has(key)) {
      return state.values.get(key) as T
    }

    try {
      const saved = persist ? sessionStorage.getItem(state.prefix + key) : null

      return saved ? (JSON.parse(saved) as T) : initial
    } catch {
      return initial
    }
  })

  const valueRef = useRef(value)
  valueRef.current = value
  const update = useCallback<Dispatch<SetStateAction<T>>>(
    action => {
      const next = typeof action === 'function' ? (action as (previous: T) => T)(valueRef.current) : action
      valueRef.current = next
      state.values.set(key, next)

      if (persist) {
        try {
          sessionStorage.setItem(state.prefix + key, JSON.stringify(next))
        } catch {
          // 浏览器存储不可用时仍保留当前登录期间的草稿。
        }
      }
      setValue(next)
    },
    [key, persist, state]
  )

  return [value, update]
}

export function usePageScroll(key: string, ready: boolean): void {
  const state = usePageStore()

  useEffect(() => {
    const content = document.querySelector<HTMLElement>('.content')
    if (!content || !ready) {
      return
    }
    const storageKey = state.prefix + `scroll:${key}`
    let position = state.values.get(storageKey)
    if (position === undefined) {
      try {
        position = Number(sessionStorage.getItem(storageKey) ?? 0)
      } catch {
        position = 0
      }
    }
    const save = () => {
      state.values.set(storageKey, content.scrollTop)
      try {
        sessionStorage.setItem(storageKey, String(content.scrollTop))
      } catch {
        // 保留内存中的阅读位置。
      }
    }
    const frame = requestAnimationFrame(() => {
      content.scrollTop = typeof position === 'number' && Number.isFinite(position) ? position : 0
      content.addEventListener('scroll', save, { passive: true })
    })

    return () => {
      cancelAnimationFrame(frame)
      content.removeEventListener('scroll', save)
    }
  }, [key, ready, state])
}
