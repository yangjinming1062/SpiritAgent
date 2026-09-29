import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { getStrings } from '@/shared/strings'

export interface PickedImage {
  base64: string
  contentType: string
  previewUrl: string
}

// 与后端 AvatarFromImageRequest 上限对齐——更大的请求会被拒为 422，
// 用户无从操作，所以这里先给提示直接拒收。
const MAX_IMAGE_BASE64 = 8 * 1024 * 1024

/** `null` when the user cancels or the file is unreadable; `error` is user-facing copy. */
export async function pickAvatarImage(title: string): Promise<{ image: PickedImage } | { error: string } | null> {
  try {
    const [path] = await window.spiritagent.selectPaths({
      title,
      filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }]
    })

    if (!path) {
      return null
    }

    const dataUrl = await window.spiritagent.readFileDataUrl(path)
    const comma = dataUrl.indexOf(',')
    const base64 = comma > 0 ? dataUrl.slice(comma + 1) : ''

    if (!base64) {
      return null
    }

    return base64.length > MAX_IMAGE_BASE64
      ? { error: getStrings().common.imagePick.tooLarge }
      : { image: { base64, contentType: dataUrl.slice(5, comma).split(';')[0], previewUrl: dataUrl } }
  } catch (error) {
    log.warn('avatar-image', 'Could not read picked image', error)

    return { error: getStrings().common.imagePick.readFailed }
  }
}

/** `null` on failure — the raw URL is the thing the renderer can't reach, so returning it would render a broken image. */
export async function resolvePortraitUrl(
  assetUrl: string | null | undefined,
  options?: { cacheOnly?: boolean; preferCache?: boolean }
): Promise<string | null> {
  if (!assetUrl) {
    return null
  }

  try {
    return await window.spiritagent.apiAsset({
      cacheOnly: options?.cacheOnly,
      preferCache: options?.preferCache,
      url: assetUrl
    })
  } catch (error) {
    log.warn('avatar-image', 'Could not resolve portrait URL', error)

    return null
  }
}

const DB_NAME = 'spiritagent_onboarding'
const STORE_NAME = 'draft_cache'
const REF_IMAGE_KEY = 'ref_image'

function openDraftDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)

    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE_NAME)
    }

    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

// 每次操作独立连接：事务提交后才算完成，失败或中止即拒绝，结束后关闭连接。
async function withDraftStore(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest): Promise<unknown> {
  const db = await openDraftDB()

  try {
    return await new Promise<unknown>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, mode)
      const request = run(tx.objectStore(STORE_NAME))
      const fail = (): void => reject(tx.error ?? request.error ?? new Error('Draft transaction aborted'))

      tx.oncomplete = () => resolve(request.result)
      tx.onerror = fail
      tx.onabort = fail
    })
  } finally {
    db.close()
  }
}

export async function saveDraftRefImage(image: PickedImage | null): Promise<void> {
  try {
    await withDraftStore('readwrite', store => (image ? store.put(image, REF_IMAGE_KEY) : store.delete(REF_IMAGE_KEY)))
  } catch (error) {
    log.warn('avatar-image', 'Could not save reference draft', error)
  }
}

export async function loadDraftRefImage(): Promise<PickedImage | null> {
  try {
    const image = (await withDraftStore('readonly', store => store.get(REF_IMAGE_KEY))) as PickedImage | undefined

    return image ?? null
  } catch (error) {
    log.warn('avatar-image', 'Could not load reference draft', error)

    return null
  }
}

export async function clearDraftRefImage(): Promise<void> {
  try {
    await withDraftStore('readwrite', store => store.delete(REF_IMAGE_KEY))
  } catch (error) {
    log.warn('avatar-image', 'Could not clear reference draft', error)
  }
}

// 草稿不分账户，换号时清除。
registerStorageClearHandler(clearDraftRefImage)
