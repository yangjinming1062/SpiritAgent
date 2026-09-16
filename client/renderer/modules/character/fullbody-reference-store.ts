import { atom } from 'nanostores'

import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import type { ImageReviseMode } from '@/shared/types/spiritagent'

import { type PickedImage, resolvePortraitUrl } from './avatar-image'
import { $activeAvatarId } from './portrait-store'

interface FullbodyReferenceState {
  avatarId: number | null
  rawUrl: string | null
  previewUrl: string | null
  busy: boolean
  error: 'load' | 'generate' | 'preview' | null
  errorMessage: string | null
}

interface ReferenceResponse {
  id: number
  seed_fullbody_url: string
}

const EMPTY_STATE: FullbodyReferenceState = {
  avatarId: null,
  rawUrl: null,
  previewUrl: null,
  busy: false,
  error: null,
  errorMessage: null
}

export const $fullbodyReference = atom<FullbodyReferenceState>(EMPTY_STATE)

let operationVersion = 0
let pending: { avatarId: number; promise: Promise<boolean> } | null = null

function referenceErrorMessage(error: unknown, fallback: string): string {
  const raw = unwrapIpcErrorMessage(error).replace(/^\d{3}\s+(?:\/[^\s]*:\s*)?/, '')

  try {
    const parsed = JSON.parse(raw) as { detail?: { error?: unknown } }
    const backendError = parsed?.detail?.error

    if (typeof backendError === 'string' && backendError) {
      return backendError
    }
  } catch {
    /* 非预期形态，走兜底文案 */
  }

  return fallback
}

function clearReference(): void {
  operationVersion += 1
  pending = null
  $fullbodyReference.set(EMPTY_STATE)
}

registerStorageClearHandler(clearReference)
$activeAvatarId.listen(clearReference)

function updateReference(
  avatarId: number,
  generate: boolean,
  feedback: string,
  reference: PickedImage | null = null,
  mode: ImageReviseMode = 'regenerate'
): Promise<boolean> {
  if ($activeAvatarId.get() !== avatarId) {
    return Promise.resolve(false)
  }

  if (pending?.avatarId === avatarId) {
    return pending.promise
  }

  const version = ++operationVersion
  const epoch = currentClearEpoch()
  const previous = $fullbodyReference.get()
  const base = previous.avatarId === avatarId ? previous : { ...EMPTY_STATE, avatarId }
  $fullbodyReference.set({ ...base, busy: true, error: null, errorMessage: null })

  const isCurrent = (): boolean =>
    version === operationVersion && epoch === currentClearEpoch() && $activeAvatarId.get() === avatarId

  const run = async (): Promise<boolean> => {
    try {
      const response = await window.spiritagent.api<ReferenceResponse>({
        path: generate ? `/api/companion/avatar/${avatarId}/fullbody/reference` : '/api/companion/avatar',
        method: generate ? 'POST' : 'GET',
        ...(generate
          ? {
              body: {
                feedback: feedback.trim() || undefined,
                mode,
                // 微调编辑上一版，不接受参考图；参考图只随重新生成发送。
                ...(mode !== 'edit' && reference
                  ? { image: reference.base64, content_type: reference.contentType }
                  : {})
              }
            }
          : {})
      })

      if (!isCurrent()) {
        return false
      }

      if (!response || response.id !== avatarId) {
        throw new Error('The active avatar changed')
      }

      const rawUrl = response.seed_fullbody_url || null
      const previewUrl = await resolvePortraitUrl(rawUrl)

      if (!isCurrent()) {
        return false
      }

      $fullbodyReference.set({
        avatarId,
        rawUrl,
        previewUrl: previewUrl ?? (rawUrl ? base.previewUrl : null),
        busy: false,
        error: rawUrl && !previewUrl ? 'preview' : null,
        errorMessage: null
      })

      return !rawUrl || Boolean(previewUrl)
    } catch (error) {
      if (isCurrent()) {
        log.warn('fullbody-reference', generate ? 'Generation failed' : 'Loading failed', error)
        $fullbodyReference.set({
          ...base,
          busy: false,
          error: generate ? 'generate' : 'load',
          errorMessage: referenceErrorMessage(error, generate ? '全身参考图生成失败，请稍后重试' : '全身参考图加载失败')
        })
      }

      return false
    }
  }

  // 跨面板保留在途请求，StrictMode 重挂载或重复点击共用同一次付费生成。
  const promise = run().finally(() => {
    if (pending?.promise === promise) {
      pending = null
    }
  })

  pending = { avatarId, promise }

  return promise
}

export function hydrateFullbodyReference(avatarId: number): Promise<boolean> {
  return updateReference(avatarId, false, '')
}

export function regenerateFullbodyReference(
  avatarId: number,
  feedback: string,
  reference: PickedImage | null = null,
  mode: ImageReviseMode = 'regenerate'
): Promise<boolean> {
  return updateReference(avatarId, true, feedback, reference, mode)
}
