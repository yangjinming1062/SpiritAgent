import { useCallback, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

// 读取 / 写入枚举形态的 URL search 参数（如 ?tab=foo）。让带标签页的视图
// 在刷新后能保留状态。导航统一使用 replace，避免点切换 tab 在 history 中堆积。
export function useRouteEnumParam<T extends string>(
  key: string,
  values: readonly T[],
  fallback: T
): [T, (next: T) => void] {
  const { hash, pathname, search } = useLocation()
  const navigate = useNavigate()

  const value = useMemo<T>(() => {
    const raw = new URLSearchParams(search).get(key)

    return raw && values.includes(raw as T) ? (raw as T) : fallback
  }, [fallback, key, search, values])

  const setValue = useCallback(
    (next: T) => {
      const params = new URLSearchParams(search)

      if (next === fallback) {
        params.delete(key)
      } else {
        params.set(key, next)
      }

      const qs = params.toString()
      void navigate({ hash, pathname, search: qs ? `?${qs}` : '' }, { replace: true })
    },
    [fallback, hash, key, navigate, pathname, search]
  )

  return [value, setValue]
}
