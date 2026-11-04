import { atom } from 'nanostores'

import { backendDetailMessage, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import {
  currentClearEpoch,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedJson
} from '@/shared/lib/storage'
import { getStrings } from '@/shared/strings'
import { isRecord } from '@client-shared/is-record'
import type { ImageReviseMode } from '@protocol'

import type { ApiSchemas } from '../../../../shared/api.generated'

import { parseImageDataUrl, type PickedImage, resolvePortraitUrl } from './avatar-image'
import { patchAvatarSeeds } from './avatar-seeds-store'
import { $activeAvatarId } from './portrait-store'

interface FullbodyReferenceState {
  avatarId: string | null
  rawUrl: string | null
  previewUrl: string | null
  busy: boolean
  error: 'load' | 'generate' | 'preview' | null
  errorMessage: string | null
  candidateId: string | null
  candidateStatus: 'pending' | 'ready' | 'failed' | null
  candidateError: string | null
  candidateChoice?: { jobId: string; revision: number; id: string } | null
  history: FullbodyReferenceVersion[]
}

interface FullbodyReferenceRecord {
  id: string
  rawUrl: string
}

type FullbodyReferenceIndex = Record<string, FullbodyReferenceRecord[]>

export interface FullbodyReferenceVersion extends FullbodyReferenceRecord {
  previewUrl: string
}

const EMPTY_STATE: FullbodyReferenceState = {
  avatarId: null,
  rawUrl: null,
  previewUrl: null,
  busy: false,
  error: null,
  errorMessage: null,
  candidateId: null,
  candidateStatus: null,
  candidateError: null,
  history: []
}

async function readCandidateChoice(
  candidate: ApiSchemas['FullbodyCandidateResponse'] | null
): Promise<FullbodyReferenceState['candidateChoice']> {
  if (!candidate?.review_job_id) {
    return null
  }

  const job = await requestResource('media.get_media_job', { path: { job_id: candidate.review_job_id } }).catch(
    () => null
  )

  const choice = job?.slots[0]?.candidates[0]

  return job && choice ? { jobId: job.id, revision: job.revision, id: choice.id } : null
}

function isFullbodyReferenceIndex(value: unknown): value is FullbodyReferenceIndex {
  if (!isRecord(value)) {
    return false
  }

  return Object.entries(value).every(
    ([avatarId, entries]) =>
      /^\d+$/.test(avatarId) &&
      Array.isArray(entries) &&
      entries.every(entry => isRecord(entry) && typeof entry.id === 'string' && typeof entry.rawUrl === 'string')
  )
}

const FULLBODY_HISTORY_STORAGE_KEY = registerCompanionStorageKey('da.companion.fullbody-reference-history')

export const $fullbodyReference = atom<FullbodyReferenceState>(EMPTY_STATE)

const MAX_HISTORY = 5
let operationVersion = 0
let pending: { avatarId: string; promise: Promise<boolean> } | null = null

function appendHistory(
  current: FullbodyReferenceVersion[],
  next: FullbodyReferenceVersion
): FullbodyReferenceVersion[] {
  const unique = current.filter(entry => entry.id !== next.id && entry.rawUrl !== next.rawUrl)

  return [...unique, next].slice(-MAX_HISTORY)
}

function historyRecord(entry: FullbodyReferenceVersion): FullbodyReferenceRecord {
  return { id: entry.id, rawUrl: entry.rawUrl }
}

function loadHistoryIndex(): FullbodyReferenceIndex {
  return storedJson<FullbodyReferenceIndex>(FULLBODY_HISTORY_STORAGE_KEY, {}, isFullbodyReferenceIndex)
}

async function resolveHistory(avatarId: string): Promise<FullbodyReferenceVersion[]> {
  const stored = loadHistoryIndex()[avatarId] ?? []

  const resolved = await Promise.all(
    stored.slice(-MAX_HISTORY).map(async entry => {
      const previewUrl = await resolvePortraitUrl(entry.rawUrl, { cacheOnly: true })

      return previewUrl ? { ...entry, previewUrl } : null
    })
  )

  return resolved.filter((entry): entry is FullbodyReferenceVersion => entry !== null)
}

function saveHistory(avatarId: string, entries: FullbodyReferenceVersion[]): void {
  const next = { ...loadHistoryIndex() }

  if (entries.length > 0) {
    next[avatarId] = entries.map(historyRecord)
  } else {
    delete next[avatarId]
  }

  persistString(FULLBODY_HISTORY_STORAGE_KEY, Object.keys(next).length > 0 ? JSON.stringify(next) : null)
}

function nextHistoryId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function clearReference(): void {
  operationVersion += 1
  pending = null
  $fullbodyReference.set(EMPTY_STATE)
}

registerStorageClearHandler(clearReference)
$activeAvatarId.listen(clearReference)

// 开始一次改写参考图状态的操作并使更早的操作作废；返回的判活函数在换号、换头像、重置或被更新的操作取代后为 false。
function beginOperation(avatarId: string): () => boolean {
  const version = ++operationVersion
  const epoch = currentClearEpoch()

  return () => version === operationVersion && epoch === currentClearEpoch() && $activeAvatarId.get() === avatarId
}

// 全身种子图写入本地缓存，自备图流程直接读取该缓存。preview 解析失败时不要显式传 null：store 的显式 display 分支会冲掉旧展示 URL，导致自备图弹窗突然缺参考图；缺省 undefined 交给 resolveDisplayUrl 保留 previous。
function syncSeeds(
  avatarId: string,
  response: ApiSchemas['AvatarAssetResponse'],
  rawUrl: string | null,
  previewUrl: string | null
): Promise<void> {
  return patchAvatarSeeds({
    avatarId,
    assetUrl: response.asset_url ?? undefined,
    fullbodySeedUrl: rawUrl,
    ...(previewUrl ? { fullbodyDisplayUrl: previewUrl } : {})
  })
}

function updateReference(
  avatarId: string,
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

  const isCurrent = beginOperation(avatarId)
  const previous = $fullbodyReference.get()
  const base = previous.avatarId === avatarId ? previous : { ...EMPTY_STATE, avatarId }
  $fullbodyReference.set({ ...base, busy: true, error: null, errorMessage: null })
  let history = base.history

  const run = async (): Promise<boolean> => {
    try {
      if (!generate) {
        history = await resolveHistory(avatarId)

        if (!isCurrent()) {
          return false
        }

        $fullbodyReference.set({ ...base, history, busy: true, error: null, errorMessage: null })
      }

      const response = generate
        ? await requestResource('companion.post_fullbody_reference', {
            path: { avatar_id: avatarId },
            body: {
              feedback: feedback.trim() || undefined,
              mode,
              ...(base.candidateId ? { candidate_id: base.candidateId } : {}),
              ...(mode !== 'edit' && reference ? { image: reference.base64, content_type: reference.contentType } : {})
            }
          })
        : await requestResource('companion.get_avatar')

      if (!isCurrent()) {
        return false
      }

      if (!response || ('avatar_id' in response ? response.avatar_id : response.id) !== avatarId) {
        throw new Error('The active avatar changed')
      }

      const asset = 'asset_url' in response ? response : null

      const candidate = generate
        ? 'avatar_id' in response
          ? response
          : null
        : await requestResource('companion.get_fullbody_candidate', {
            path: { avatar_id: avatarId }
          })

      if (!isCurrent()) {
        return false
      }

      const rawUrl = candidate?.image_url || asset?.seed_fullbody_url || null
      const candidateChoice = await readCandidateChoice(candidate)

      if (generate && !rawUrl) {
        throw new Error('The fullbody reference response has no image')
      }

      const previewUrl = await resolvePortraitUrl(rawUrl, { preferCache: true })

      if (!isCurrent()) {
        return false
      }

      if (generate && !candidate) {
        history = base.history
      }

      if (generate && !candidate && base.rawUrl && base.previewUrl && rawUrl !== base.rawUrl) {
        history = appendHistory(history, {
          id: nextHistoryId(),
          rawUrl: base.rawUrl,
          previewUrl: base.previewUrl
        })
        saveHistory(avatarId, history)
      }

      $fullbodyReference.set({
        avatarId,
        rawUrl,
        previewUrl: previewUrl ?? (rawUrl ? base.previewUrl : null),
        busy: false,
        error: rawUrl && !previewUrl ? 'preview' : null,
        errorMessage: null,
        candidateId: candidate?.id ?? null,
        candidateChoice,
        candidateStatus:
          candidate?.status === 'pending' || candidate?.status === 'ready' || candidate?.status === 'failed'
            ? candidate.status
            : null,
        candidateError: candidate?.error ?? null,
        history
      })

      if (!candidate && asset) {
        await syncSeeds(avatarId, asset, rawUrl, previewUrl)
      }

      return isCurrent() && (!rawUrl || Boolean(previewUrl))
    } catch (error) {
      if (isCurrent()) {
        log.warn('fullbody-reference', generate ? 'Generation failed' : 'Loading failed', error)
        $fullbodyReference.set({
          ...base,
          busy: false,
          history,
          error: generate ? 'generate' : 'load',
          // 无后端公开文案时由面板按 error 显示本地化提示。
          errorMessage: backendDetailMessage(error, '') || null
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

export async function restoreFullbodyReferenceVersion(avatarId: string, versionId: string): Promise<boolean> {
  const current = $fullbodyReference.get()
  const versionToRestore = current.history.find(entry => entry.id === versionId)

  if (current.avatarId !== avatarId || !versionToRestore || current.busy || $activeAvatarId.get() !== avatarId) {
    return false
  }

  const image = parseImageDataUrl(versionToRestore.previewUrl)

  if (!image) {
    return false
  }

  const isCurrent = beginOperation(avatarId)
  $fullbodyReference.set({ ...current, busy: true, error: null, errorMessage: null })

  try {
    const response = await requestResource('companion.post_fullbody_adopt', {
      path: { avatar_id: avatarId },
      body: { image: image.base64, content_type: image.contentType }
    })

    if (!isCurrent()) {
      return false
    }

    if (!response || ('avatar_id' in response ? response.avatar_id : response.id) !== avatarId) {
      throw new Error('The active avatar changed')
    }

    const rawUrl = 'image_url' in response ? response.image_url : response.seed_fullbody_url || null
    const candidateChoice = await readCandidateChoice('avatar_id' in response ? response : null)
    const previewUrl = await resolvePortraitUrl(rawUrl, { preferCache: true })

    if (!isCurrent()) {
      return false
    }

    let history = current.history.filter(entry => entry.id !== versionId)

    if (
      current.error !== 'load' &&
      current.error !== 'preview' &&
      current.rawUrl &&
      current.previewUrl &&
      current.rawUrl !== rawUrl
    ) {
      history = appendHistory(history, {
        id: nextHistoryId(),
        rawUrl: current.rawUrl,
        previewUrl: current.previewUrl
      })
    }

    saveHistory(avatarId, history)

    $fullbodyReference.set({
      ...current,
      rawUrl,
      previewUrl: previewUrl ?? current.previewUrl,
      busy: false,
      error: rawUrl && !previewUrl ? 'preview' : null,
      errorMessage: null,
      candidateId: 'avatar_id' in response ? response.id : null,
      candidateChoice,
      candidateStatus:
        'avatar_id' in response &&
        (response.status === 'pending' || response.status === 'ready' || response.status === 'failed')
          ? response.status
          : null,
      candidateError: 'error' in response ? response.error : null,
      history
    })

    if ('asset_url' in response) {
      await syncSeeds(avatarId, response, rawUrl, previewUrl)
    }

    return Boolean(rawUrl && previewUrl)
  } catch (error) {
    if (isCurrent()) {
      log.warn('fullbody-reference', 'History restore failed', error)
      $fullbodyReference.set({
        ...current,
        busy: false,
        error: 'generate',
        errorMessage: getStrings().settings.persona.fullbodyReference.restoreFailed
      })
    }

    return false
  }
}

export function clearFullbodyReferenceHistory(avatarId: string): void {
  saveHistory(avatarId, [])
  const current = $fullbodyReference.get()

  if (current.avatarId === avatarId) {
    $fullbodyReference.set({ ...current, history: [] })
  }
}

export function hydrateFullbodyReference(avatarId: string): Promise<boolean> {
  return updateReference(avatarId, false, '')
}

export function regenerateFullbodyReference(
  avatarId: string,
  feedback: string,
  reference: PickedImage | null = null,
  mode: ImageReviseMode = 'regenerate'
): Promise<boolean> {
  return updateReference(avatarId, true, feedback, reference, mode)
}

export async function acceptFullbodyCandidate(avatarId: string): Promise<boolean> {
  const current = $fullbodyReference.get()

  if (current.avatarId !== avatarId || !current.candidateId || current.busy) {
    return false
  }

  const isCurrent = beginOperation(avatarId)
  $fullbodyReference.set({ ...current, busy: true, errorMessage: null })

  try {
    let response: ApiSchemas['AvatarAssetResponse']

    if (current.candidateStatus === 'ready') {
      response = await requestResource('companion.post_fullbody_candidate_accept', {
        path: { avatar_id: avatarId, candidate_id: current.candidateId }
      })
    } else {
      const choice = current.candidateChoice

      if (!choice) {
        throw new Error('原候选版本尚未核对，请刷新后重试')
      }

      const result = await requestResource('media.manually_handle_media', {
        path: { job_id: choice.jobId },
        body: {
          request_id: crypto.randomUUID(),
          action: 'approve',
          slot: 0,
          candidate_id: choice.id,
          expected_revision: choice.revision
        }
      })

      if (!result.complete) {
        throw new Error(result.slots[0]?.error || '候选状态已变化，请刷新后重试')
      }

      response = await requestResource('companion.get_avatar')
    }

    if (!isCurrent()) {
      return false
    }

    const rawUrl = response.seed_fullbody_url || null
    const previewUrl = await resolvePortraitUrl(rawUrl)

    if (!isCurrent()) {
      return false
    }

    $fullbodyReference.set({
      avatarId,
      rawUrl,
      previewUrl,
      busy: false,
      error: rawUrl && !previewUrl ? 'preview' : null,
      errorMessage: null,
      candidateId: null,
      candidateStatus: null,
      candidateError: null,
      history: current.history
    })
    await syncSeeds(avatarId, response, rawUrl, previewUrl)

    return Boolean(rawUrl && previewUrl)
  } catch (error) {
    if (!isCurrent()) {
      return false
    }

    const httpStatus = ipcErrorStatus(error)

    if (httpStatus === null || httpStatus >= 500 || httpStatus === 404 || httpStatus === 409) {
      const epoch = currentClearEpoch()
      const hydrated = await hydrateFullbodyReference(avatarId)
      const latest = $fullbodyReference.get()

      return (
        hydrated &&
        epoch === currentClearEpoch() &&
        $activeAvatarId.get() === avatarId &&
        latest.avatarId === avatarId &&
        latest.candidateId === null &&
        Boolean(current.rawUrl && latest.rawUrl?.split('?')[0] === current.rawUrl.split('?')[0])
      )
    }

    $fullbodyReference.set({
      ...current,
      busy: false,
      candidateError: backendDetailMessage(error, getStrings().settings.persona.fullbodyReference.acceptCandidateFailed)
    })

    return false
  }
}
