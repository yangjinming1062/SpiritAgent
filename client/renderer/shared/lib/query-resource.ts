import { atom } from 'nanostores'

import { captureAuthScope } from './authed-api'
import { log } from './log'
import { registerStorageClearHandler } from './storage'

export function createQueryResource<T>(fallback: T, owner = 'resource') {
  const $data = atom(fallback)
  const $loading = atom(false)
  const $error = atom(false)
  let request = 0
  let queryKey: string | null = null

  const clear = (): void => {
    request++
    queryKey = null
    $data.set(fallback)
    $loading.set(false)
    $error.set(false)
  }

  registerStorageClearHandler(clear)

  async function load(key: string, read: (current: () => boolean) => Promise<T>): Promise<void> {
    const scope = captureAuthScope()

    if (!scope) {
      return
    }

    const token = ++request
    const current = (): boolean => scope() && token === request

    if (key !== queryKey) {
      queryKey = key
      $data.set(fallback)
    }

    $loading.set(true)
    $error.set(false)

    try {
      const data = await read(current)

      if (current()) {
        $data.set(data)
      }
    } catch (error) {
      if (current()) {
        log.warn(owner, 'Could not load resource', error)
        $error.set(true)
      }
    } finally {
      if (current()) {
        $loading.set(false)
      }
    }
  }

  return { $data, $loading, $error, load, clear }
}
