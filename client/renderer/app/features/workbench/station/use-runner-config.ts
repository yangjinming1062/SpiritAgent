import type React from 'react'
import { useEffect } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { notifyError } from '@/shared/store/notifications'

type Config = Record<string, unknown>

export function getIn(obj: unknown, path: readonly (string | number)[]): unknown {
  let cur: unknown = obj

  for (const key of path) {
    if (cur == null || typeof cur !== 'object') {
      return undefined
    }

    cur = (cur as Record<string | number, unknown>)[key]
  }

  return cur
}

export function setIn(obj: Config, path: readonly (string | number)[], value: unknown): Config {
  if (path.length === 0) {
    return obj
  }

  const [key, ...rest] = path
  const clone = (Array.isArray(obj) ? [...obj] : { ...obj }) as Record<string | number, unknown>
  clone[key] = rest.length === 0 ? value : setIn((clone[key] as Config) ?? {}, rest, value)

  return clone as Config
}

interface UseRunnerConfigResult {
  config: Config | null
  setConfig: React.Dispatch<React.SetStateAction<Config | null>>
  isLoading: boolean
  patch: (path: readonly string[], value: unknown) => Promise<void>
}

export function useRunnerConfig(errorKey: string): UseRunnerConfigResult {
  const loader = useAsyncLoader<Config>(async () => {
    const result = await window.spiritagent.runnerConfig.read()

    if (!result.ok) {
      throw new Error(result.error)
    }

    return result.config
  })

  useEffect(() => {
    if (loader.error) {
      notifyError(loader.error, errorKey)
    }
  }, [loader.error, errorKey])

  const patch = async (path: readonly string[], value: unknown): Promise<void> => {
    const res = await window.spiritagent.runnerConfig.patch({ path, value })

    if (!res.ok) {
      throw new Error(res.error || 'unknown error')
    }
  }

  return { config: loader.data, setConfig: loader.setData, isLoading: loader.isLoading, patch }
}
