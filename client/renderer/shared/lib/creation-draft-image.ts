import { isRecord } from '@client-shared/is-record'

import { log } from './log'
import {
  accountStorageKey,
  persistString,
  registerAccountCacheRemovalHandler,
  registerCompanionStorageKey
} from './storage'

interface CreationDraftImage {
  base64: string
  contentType: string
  previewUrl: string
}

const IMAGE_CACHE_KEY = registerCompanionStorageKey('da.creation.references.v1')
const pendingWrites = new Map<string, Promise<void>>()
const removingAccounts = new Set<string>()

// 图片字节放入 IndexedDB，避免占满保存导航与文字的 localStorage。
async function withImageStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('spiritagent_creation_drafts', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('images')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })

  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = db.transaction('images', mode)
      const request = run(transaction.objectStore('images'))
      transaction.oncomplete = () => resolve(request.result)
      transaction.onerror = () => reject(transaction.error ?? request.error)
      transaction.onabort = () => reject(transaction.error ?? request.error)
    })
  } finally {
    db.close()
  }
}

export async function saveCreationDraftImage(slot: string, image: CreationDraftImage | null): Promise<void> {
  const accountKey = accountStorageKey(IMAGE_CACHE_KEY)

  if (!accountKey || removingAccounts.has(accountKey)) {
    return
  }

  const key = `${accountKey}:${slot}`

  const write = (pendingWrites.get(key) ?? Promise.resolve()).then(async () => {
    if (removingAccounts.has(accountKey)) {
      return
    }

    try {
      if (image) {
        await withImageStore('readwrite', store =>
          store.put({ base64: image.base64, contentType: image.contentType }, key)
        )
      } else {
        await withImageStore('readwrite', store => store.delete(key))
      }

      if (accountKey === accountStorageKey(IMAGE_CACHE_KEY)) {
        persistString(IMAGE_CACHE_KEY, JSON.stringify({ slot, revision: crypto.randomUUID() }))
      }
    } catch (error) {
      log.warn('creation-draft', 'Could not save reference image', error)
    }
  })

  pendingWrites.set(key, write)
  await write

  if (pendingWrites.get(key) === write) {
    pendingWrites.delete(key)
  }
}

export async function loadCreationDraftImage(slot: string): Promise<CreationDraftImage | null> {
  const accountKey = accountStorageKey(IMAGE_CACHE_KEY)

  if (!accountKey) {
    return null
  }

  try {
    const key = `${accountKey}:${slot}`
    await pendingWrites.get(key)
    const image: unknown = await withImageStore('readonly', store => store.get(key))

    if (
      !isRecord(image) ||
      typeof image.base64 !== 'string' ||
      typeof image.contentType !== 'string' ||
      !image.contentType.startsWith('image/')
    ) {
      return null
    }

    return {
      base64: image.base64,
      contentType: image.contentType,
      previewUrl: `data:${image.contentType};base64,${image.base64}`
    }
  } catch (error) {
    log.warn('creation-draft', 'Could not restore reference image', error)

    return null
  }
}

export function subscribeCreationDraftImage(slot: string, onChange: () => void): () => void {
  const changed = (event: StorageEvent): void => {
    if (event.key !== accountStorageKey(IMAGE_CACHE_KEY) || !event.newValue) {
      return
    }

    try {
      const notification: unknown = JSON.parse(event.newValue)

      if (isRecord(notification) && notification.slot === slot) {
        onChange()
      }
    } catch {
      return
    }
  }

  window.addEventListener('storage', changed)

  return () => window.removeEventListener('storage', changed)
}

registerAccountCacheRemovalHandler(async accountId => {
  const accountKey = accountStorageKey(IMAGE_CACHE_KEY, accountId)

  if (!accountKey) {
    return
  }

  const prefix = `${accountKey}:`
  removingAccounts.add(accountKey)

  try {
    await Promise.all([...pendingWrites].filter(([key]) => key.startsWith(prefix)).map(([, write]) => write))
    await withImageStore('readwrite', store => {
      const request = store.openCursor()

      request.onsuccess = () => {
        const cursor = request.result

        if (!cursor) {
          return
        }

        if (String(cursor.key).startsWith(prefix)) {
          cursor.delete()
        }

        cursor.continue()
      }

      return request
    })
  } finally {
    removingAccounts.delete(accountKey)
  }
})
