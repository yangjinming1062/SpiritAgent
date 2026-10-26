import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useCallback, useEffect } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import {
  loadCreationDraftImage,
  saveCreationDraftImage,
  subscribeCreationDraftImage
} from '@/shared/lib/creation-draft-image'
import { backendDetailMessage, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'
import type { ImageReviseMode } from '@protocol'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { ApiError } from '../../../../../shared/errors'
import { pickAvatarImage, type PickedImage, resolvePortraitUrl } from '../avatar-image'

import { $outfits, $wardrobeStatus, hydrateWardrobe } from './wardrobe-store'

interface DesignMessage {
  id: number
  role: 'user' | 'system'
  text: string
  imageUrl?: string
  tone?: 'error' | 'info'
}

interface DesignDraft {
  id: string
  previewUrl: string
  sourcePath: string | null
}

interface DesignState {
  messages: DesignMessage[]
  draft: DesignDraft | null
  refImage: PickedImage | null
  stage: 'generating' | 'confirming' | null
  restoringReference: boolean
  resultUnknown: boolean
  inspected: boolean
  previewError: boolean
  lastRequest: { image: PickedImage | null; text: string; mode: ImageReviseMode } | null
  revision: number
  referenceRevision: number
}

const EMPTY_STATE: DesignState = {
  messages: [],
  draft: null,
  refImage: null,
  stage: null,
  restoringReference: false,
  resultUnknown: false,
  inspected: false,
  previewError: false,
  lastRequest: null,
  revision: 0,
  referenceRevision: 0
}

// 在途操作按草稿持有，离页只解除视图回调，结果仍写回原草稿；null 为尚未生成的新设计。
const $sessions = atom<ReadonlyMap<string | null, DesignState>>(new Map())
let nextRevision = 0
let nextMessageId = 0

registerStorageClearHandler(() => $sessions.set(new Map()))

function readSession(id: string | null): DesignState {
  return $sessions.get().get(id) ?? EMPTY_STATE
}

function updateSession(id: string | null, patch: Partial<DesignState>): void {
  const sessions = new Map($sessions.get())
  sessions.set(id, { ...readSession(id), ...patch })
  $sessions.set(sessions)
}

function push(id: string | null, message: Omit<DesignMessage, 'id'>): void {
  updateSession(id, { messages: [...readSession(id).messages, { ...message, id: ++nextMessageId }] })
}

function isCurrent(id: string | null, revision: number, scope: () => boolean): boolean {
  return scope() && readSession(id).revision === revision
}

function resultUnknown(error: unknown): boolean {
  const status = ipcErrorStatus(error)

  return status === null || (status >= 500 && !(error instanceof ApiError && error.code === 'generation_failed'))
}

// 结果不明时保留所属草稿的提交保护，切换设计不能释放它。
export function useOutfitDesignSession(
  selectedDraftId: string | null,
  onConfirmed: (outfitId: string) => void,
  onDrafted: (outfitId: string, submittedText: string) => void
): {
  messages: DesignMessage[]
  draft: DesignDraft | null
  refImage: PickedImage | null
  busy: boolean
  stage: 'loading' | 'generating' | 'confirming' | null
  resultUnknown: boolean
  inspected: boolean
  previewError: boolean
  lastRequest: DesignState['lastRequest']
  tasks: ReadonlyMap<string | null, 'generating' | 'confirming' | 'unknown'>
  send: (text: string, mode: ImageReviseMode) => void
  retry: () => void
  confirm: () => Promise<void>
  attachRefImage: () => Promise<void>
  clearRefImage: () => void
  adoptDraft: (id: string, previewUrl: string, sourcePath: string | null) => void
  inspectResult: () => Promise<void>
  finishWaiting: () => void
  restoreReference: () => void
} {
  const sessions = useStore($sessions)
  const state = sessions.get(selectedDraftId) ?? EMPTY_STATE
  const outfits = useStore($outfits)
  const wardrobeStatus = useStore($wardrobeStatus)
  const authKind = useStore($auth).kind
  const begin = useAsyncGuard()

  useEffect(() => {
    if (wardrobeStatus !== 'loaded') {
      return
    }

    const drafts = new Set(outfits.filter(outfit => outfit.status === 'draft').map(outfit => outfit.id))
    const next = new Map($sessions.get())

    for (const [id, session] of next) {
      if (id !== null && !session.stage && !drafts.has(id)) {
        next.delete(id)
      }
    }

    if (next.size !== $sessions.get().size) {
      $sessions.set(next)
    }
  }, [outfits, sessions, wardrobeStatus])

  const restoreReference = useCallback((): void => {
    if (selectedDraftId !== null || readSession(null).stage) {
      return
    }

    const scope = captureAuthScope()

    if (!scope) {
      return
    }

    const referenceRevision = ++nextRevision
    updateSession(null, { restoringReference: true, referenceRevision })
    void loadCreationDraftImage('outfit').then(image => {
      if (scope() && readSession(null).referenceRevision === referenceRevision) {
        updateSession(null, { refImage: image, restoringReference: false })
      }
    })
  }, [selectedDraftId])

  useEffect(() => {
    if (authKind === 'authenticated') {
      restoreReference()
    }

    return subscribeCreationDraftImage('outfit', restoreReference)
  }, [authKind, restoreReference])

  const runDesign = useCallback(
    (text: string, image: PickedImage | null, mode: ImageReviseMode): void => {
      const scope = captureAuthScope()
      const isLive = begin()
      let id = selectedDraftId
      const current = readSession(id)
      const withDraft = current.draft

      if (
        !scope ||
        !isLive() ||
        current.stage ||
        current.restoringReference ||
        current.resultUnknown ||
        (id !== null && !withDraft)
      ) {
        return
      }

      const revision = ++nextRevision
      updateSession(id, {
        revision,
        stage: 'generating',
        resultUnknown: false,
        inspected: false,
        previewError: false,
        lastRequest: null,
        refImage: null,
        referenceRevision: ++nextRevision
      })
      push(id, {
        imageUrl: image?.previewUrl,
        role: 'user',
        text: text || getStrings().living.outfit.design.byReference
      })

      void (async () => {
        try {
          const res = withDraft ? await runRegenerate(withDraft.id, text, mode) : await runCreate(text, image)

          if (scope()) {
            void hydrateWardrobe({ force: true })
          }

          if (!isCurrent(id, revision, scope)) {
            return
          }

          const rawUrl = res?.fullbody_url || null

          if (!res?.id || !rawUrl) {
            throw new Error('invalid outfit response')
          }

          // 先绑定服务端 ID，避免列表先显示新草稿时绕过仍在加载图片的任务。
          if (id === null) {
            void saveCreationDraftImage('outfit', null)
            const next = new Map($sessions.get())
            next.set(res.id, readSession(null))
            next.delete(null)
            $sessions.set(next)
            id = res.id
          }

          if (isLive()) {
            onDrafted(res.id, text)
          }

          const resolved = await resolvePortraitUrl(rawUrl)

          if (!isCurrent(id, revision, scope)) {
            return
          }

          updateSession(id, {
            draft: {
              id: res.id,
              previewUrl: resolved ?? withDraft?.previewUrl ?? '',
              sourcePath: rawUrl.split('?')[0]
            },
            previewError: !resolved
          })
          push(id, {
            role: 'system',
            text: !resolved
              ? getStrings().living.outfit.design.previewFailed
              : withDraft && mode === 'edit'
                ? getStrings().living.outfit.design.refined
                : getStrings().living.outfit.design.drafted,
            tone: 'info'
          })
        } catch (err) {
          if (!isCurrent(id, revision, scope)) {
            return
          }

          const timedOut = err instanceof ApiError && err.code === 'timeout'
          const unknown = resultUnknown(err)
          updateSession(id, { resultUnknown: unknown, inspected: false, lastRequest: { image, text, mode } })
          push(id, {
            role: 'system',
            text: unknown
              ? getStrings().living.outfit.design.timedOut
              : backendDetailMessage(err, getStrings().living.outfit.design.generateFailed),
            tone: unknown ? 'info' : 'error'
          })
          log.warn('wardrobe-design', timedOut ? 'generation result timed out' : 'generation failed', err)
        } finally {
          if (isCurrent(id, revision, scope)) {
            updateSession(id, { stage: null })
          }
        }
      })()
    },
    [begin, onDrafted, selectedDraftId]
  )

  const send = useCallback(
    (text: string, mode: ImageReviseMode = 'edit'): void => {
      const current = readSession(selectedDraftId)
      const trimmed = text.trim()

      if (current.previewError || (!current.draft && !trimmed && !current.refImage)) {
        return
      }

      runDesign(trimmed, current.refImage, current.draft ? mode : 'edit')
    },
    [runDesign, selectedDraftId]
  )

  const retry = useCallback((): void => {
    const { lastRequest } = readSession(selectedDraftId)

    if (lastRequest) {
      runDesign(lastRequest.text, lastRequest.image, lastRequest.mode)
    }
  }, [runDesign, selectedDraftId])

  const confirm = useCallback(async (): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = begin()
    const id = selectedDraftId
    const current = readSession(id)
    const { draft } = current

    if (!scope || !isLive() || !draft?.previewUrl || current.stage || current.resultUnknown || current.previewError) {
      return
    }

    const revision = ++nextRevision
    updateSession(id, { revision, stage: 'confirming' })

    try {
      await requestResource('companion.post_outfit_confirm', { path: { outfit_id: draft.id } })

      if (scope()) {
        void hydrateWardrobe({ force: true })
      }

      if (isCurrent(id, revision, scope)) {
        updateSession(id, { draft: null, messages: [] })

        if (isLive()) {
          onConfirmed(draft.id)
        }
      }
    } catch (err) {
      if (!isCurrent(id, revision, scope)) {
        return
      }

      push(id, {
        role: 'system',
        text: backendDetailMessage(err, getStrings().living.outfit.confirmFailed),
        tone: 'error'
      })
      updateSession(id, { resultUnknown: resultUnknown(err), inspected: false })
      log.warn('wardrobe-design', 'confirm failed', err)
    } finally {
      if (isCurrent(id, revision, scope)) {
        updateSession(id, { stage: null })
      }
    }
  }, [begin, onConfirmed, selectedDraftId])

  const attachRefImage = useCallback(async (): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = begin()

    if (!scope || selectedDraftId !== null || readSession(null).stage) {
      return
    }

    const referenceRevision = ++nextRevision
    updateSession(null, { referenceRevision, restoringReference: false })
    const picked = await pickAvatarImage(getStrings().living.outfit.design.pickReferenceTitle)

    if (!picked || !scope() || !isLive() || readSession(null).referenceRevision !== referenceRevision) {
      return
    }

    if ('error' in picked) {
      push(null, { role: 'system', text: picked.error, tone: 'error' })

      return
    }

    updateSession(null, { refImage: picked.image })
    void saveCreationDraftImage('outfit', picked.image)
  }, [begin, selectedDraftId])

  const clearRefImage = useCallback((): void => {
    updateSession(null, { referenceRevision: ++nextRevision, restoringReference: false, refImage: null })
    void saveCreationDraftImage('outfit', null)
  }, [])

  const adoptDraft = useCallback((id: string, previewUrl: string, sourcePath: string | null): void => {
    const current = readSession(id)
    const path = sourcePath?.split('?')[0] ?? null
    const preview = previewUrl || current.draft?.previewUrl || ''
    const reconciled = current.resultUnknown && path !== null && current.draft?.sourcePath !== path

    if (
      current.stage ||
      (!reconciled &&
        current.draft &&
        current.draft.sourcePath === path &&
        current.draft.previewUrl === preview &&
        current.previewError === !previewUrl)
    ) {
      return
    }

    updateSession(id, {
      draft: { id, previewUrl: preview, sourcePath: path },
      previewError: !previewUrl,
      ...(reconciled ? { resultUnknown: false, inspected: false, lastRequest: null } : {})
    })

    if (current.messages.length === 0) {
      push(id, {
        role: 'system',
        text: previewUrl
          ? getStrings().living.outfit.design.resumeReady
          : getStrings().living.outfit.design.resumePreviewMissing,
        tone: 'info'
      })
    }
  }, [])

  const inspectResult = useCallback(async (): Promise<void> => {
    const scope = captureAuthScope()
    const current = readSession(selectedDraftId)

    if (!scope || !current.resultUnknown || current.stage) {
      return
    }

    updateSession(selectedDraftId, { inspected: false })
    await hydrateWardrobe({ force: true })

    if (isCurrent(selectedDraftId, current.revision, scope) && $wardrobeStatus.get() === 'loaded') {
      updateSession(selectedDraftId, { inspected: true })
    }
  }, [selectedDraftId])

  const finishWaiting = useCallback((): void => {
    const current = readSession(selectedDraftId)

    if (current.resultUnknown && current.inspected && !current.stage) {
      updateSession(selectedDraftId, {
        revision: ++nextRevision,
        resultUnknown: false,
        inspected: false,
        refImage: current.refImage ?? current.lastRequest?.image ?? null,
        lastRequest: null
      })
    }
  }, [selectedDraftId])

  const tasks = new Map<string | null, 'generating' | 'confirming' | 'unknown'>()

  for (const [id, session] of sessions) {
    if (session.stage || session.resultUnknown) {
      tasks.set(id, session.stage ?? 'unknown')
    }
  }

  return {
    messages: state.messages,
    draft: state.draft,
    refImage: state.refImage,
    busy: Boolean(state.stage) || state.restoringReference,
    stage: state.restoringReference ? 'loading' : state.stage,
    resultUnknown: state.resultUnknown,
    inspected: state.inspected,
    previewError: state.previewError,
    lastRequest: state.lastRequest,
    tasks,
    send,
    retry,
    confirm,
    attachRefImage,
    clearRefImage,
    adoptDraft,
    inspectResult,
    finishWaiting,
    restoreReference
  }
}

async function runCreate(description: string, image: PickedImage | null): Promise<ApiSchemas['OutfitResponse']> {
  return requestResource('companion.post_outfit', {
    body: {
      source: 'generate',
      description: description || undefined,
      image: image?.base64,
      content_type: image?.contentType
    }
  })
}

async function runRegenerate(
  id: string,
  feedback: string,
  mode: ImageReviseMode
): Promise<ApiSchemas['OutfitResponse']> {
  return requestResource('companion.post_outfit_regenerate', {
    path: { outfit_id: id },
    body: { source: 'generate', feedback: feedback || undefined, mode }
  })
}
