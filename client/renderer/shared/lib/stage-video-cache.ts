import { backendApi } from '../platform/desktop'

import { captureAuthScope } from './authed-api'
import { registerStorageClearHandler } from './storage'

export interface StageVideoLease {
  url: string
  release: () => void
}

interface Entry {
  references: number
  url: string | null
  request: Promise<string>
}

const entries = new Map<string, Entry>()
let generation = 0

/** 主进程负责跨窗口磁盘缓存；本窗口的并发消费者共用对象地址。 */
export async function acquireStageVideo(url: string): Promise<StageVideoLease> {
  const key = url.split('?')[0]
  let entry = entries.get(key)

  if (!entry) {
    const scope = captureAuthScope()
    const epoch = generation
    const created: Entry = { references: 0, url: null, request: Promise.resolve('') }
    created.request = backendApi()
      .apiAssetBuffer({ url, preferCache: true })
      .then(bytes => {
        if (!scope?.() || generation !== epoch || entries.get(key) !== created) {
          throw new Error('Stage media account changed')
        }

        if (!bytes?.byteLength) {
          throw new Error('Stage media is empty')
        }

        created.url = URL.createObjectURL(
          new Blob([new Uint8Array(bytes)], {
            type: key.endsWith('.webm') ? 'video/webm' : 'video/mp4'
          })
        )

        return created.url
      })
    entry = created
    entries.set(key, entry)
  }

  const held = entry
  held.references += 1
  let released = false

  const release = (): void => {
    if (released) {
      return
    }

    released = true
    held.references -= 1

    if (held.references === 0 && entries.get(key) === held) {
      entries.delete(key)

      if (held.url) {
        URL.revokeObjectURL(held.url)
      }
    }
  }

  try {
    return { url: await held.request, release }
  } catch (error) {
    release()
    throw error
  }
}

registerStorageClearHandler(() => {
  generation += 1

  for (const entry of entries.values()) {
    if (entry.url) {
      URL.revokeObjectURL(entry.url)
    }
  }

  entries.clear()
})
