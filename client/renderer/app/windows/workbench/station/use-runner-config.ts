import type React from 'react'
import { useEffect, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { notifyError } from '@/shared/store/notifications'

type SaveResult = { ok: true } | { ok: false; error: string }

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
  patch: (path: readonly string[], value: unknown) => Promise<SaveResult>
}

export function useRunnerConfig(errorKey: string): UseRunnerConfigResult {
  const [config, setConfig] = useState<Config | null>(null)

  const loader = useAsyncLoader(async () => {
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

  useEffect(() => {
    if (loader.data) {
      setConfig(loader.data)
    }
  }, [loader.data])

  const toSaveResult = (res: Awaited<ReturnType<typeof window.spiritagent.runnerConfig.patch>>): SaveResult =>
    res.ok ? { ok: true } : { ok: false, error: res.error || 'unknown error' }

  const patch = async (path: readonly string[], value: unknown): Promise<SaveResult> =>
    toSaveResult(await window.spiritagent.runnerConfig.patch({ path, value }))

  return { config, setConfig, isLoading: loader.isLoading, patch }
}
