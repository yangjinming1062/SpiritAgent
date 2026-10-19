import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
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

import { hydrateWardrobe } from './wardrobe-store'

interface DesignMessage {
  id: number
  role: 'user' | 'system'
  text: string
  imageUrl?: string
  tone?: 'error' | 'info'
}

interface DesignDraft {
  id: number
  previewUrl: string
}

// 衣柜设计会话持有输入、草稿与提交状态；结果不明时先在衣柜核对，不能直接重复制作。
export function useOutfitDesignSession(
  onConfirmed: (outfitId: number) => void,
  onDrafted?: (outfitId: number) => void
): {
  messages: DesignMessage[]
  draft: DesignDraft | null
  refImage: PickedImage | null
  busy: boolean
  stage: 'loading' | 'generating' | 'confirming' | null
  resultUnknown: boolean
  previewError: boolean
  lastRequest: { image: PickedImage | null; text: string; mode: ImageReviseMode } | null
  send: (text: string, mode: ImageReviseMode) => void
  retry: () => void
  confirm: () => Promise<void>
  attachRefImage: () => Promise<void>
  clearRefImage: () => void
  adoptDraft: (id: number, previewUrl: string) => void
  restoreReference: () => void
  reset: () => void
} {
  const [messages, setMessages] = useState<DesignMessage[]>([])
  const authKind = useStore($auth).kind
  const [draft, setDraft] = useState<DesignDraft | null>(null)
  const [refImage, setRefImage] = useState<PickedImage | null>(null)
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState<'generating' | 'confirming' | null>(null)
  const [resultUnknown, setResultUnknown] = useState(false)
  const [previewError, setPreviewError] = useState(false)
  const [restoringReference, setRestoringReference] = useState(true)

  const [lastRequest, setLastRequest] = useState<{
    image: PickedImage | null
    text: string
    mode: ImageReviseMode
  } | null>(null)

  const begin = useAsyncGuard()
  const onDraftedRef = useLatestRef(onDrafted)
  const generatingRef = useRef(false)
  const msgIdRef = useRef(0)
  const revisionRef = useRef(0)
  const referenceRevisionRef = useRef(0)

  const reset = useCallback((): void => {
    revisionRef.current += 1
    referenceRevisionRef.current += 1
    generatingRef.current = false
    setBusy(false)
    setStage(null)
    setResultUnknown(false)
    setPreviewError(false)
    setMessages([])
    setDraft(null)
    setRefImage(null)
    setRestoringReference(false)
    setLastRequest(null)
  }, [])

  useEffect(() => {
    const unregister = registerStorageClearHandler(reset)

    return () => {
      unregister()
      revisionRef.current += 1
    }
  }, [reset])

  const restoreReference = useCallback((): void => {
    const isLive = begin()
    const revision = revisionRef.current
    const referenceRevision = ++referenceRevisionRef.current
    setRestoringReference(true)
    void loadCreationDraftImage('outfit').then(image => {
      if (isLive() && revision === revisionRef.current && referenceRevision === referenceRevisionRef.current) {
        setRefImage(image)
        setRestoringReference(false)
      }
    })
  }, [begin])

  useEffect(() => {
    if (authKind === 'authenticated') {
      restoreReference()
    }

    return subscribeCreationDraftImage('outfit', () => {
      if (generatingRef.current) {
        reset()
      }

      restoreReference()
    })
  }, [authKind, reset, restoreReference])

  // isLive 来自发起操作时的 begin()；revision 被 reset、卸载或更新的独占操作递增后，旧操作即失效。
  const isCurrent = useCallback((revision: number, isLive: () => boolean): boolean => {
    return isLive() && revision === revisionRef.current
  }, [])

  const push = useCallback((message: Omit<DesignMessage, 'id'>): void => {
    msgIdRef.current += 1
    setMessages(prev => [...prev, { ...message, id: msgIdRef.current }])
  }, [])

  const runDesign = useCallback(
    (text: string, image: PickedImage | null, withDraft: DesignDraft | null, mode: ImageReviseMode): void => {
      const isLive = begin()

      if (!isLive() || generatingRef.current) {
        return
      }

      const revision = ++revisionRef.current
      generatingRef.current = true
      setBusy(true)
      setStage('generating')
      setResultUnknown(false)
      setPreviewError(false)
      setLastRequest(null)

      push({ imageUrl: image?.previewUrl, role: 'user', text: text || getStrings().living.outfit.design.byReference })

      void (async () => {
        try {
          // 有草稿后按用户意图走微调或重新生成（后端 regenerate 均不收图）；参考图仅用于首次生成。
          const res = withDraft ? await runRegenerate(withDraft.id, text, mode) : await runCreate(text, image)

          // 切换草稿后旧任务只刷新库，不再覆盖当前选择。
          if (isLive()) {
            void hydrateWardrobe()
          }

          if (!isCurrent(revision, isLive)) {
            return
          }

          const rawUrl = res?.fullbody_url || null

          if (!res?.id || !rawUrl) {
            throw new Error('invalid outfit response')
          }

          const resolved = await resolvePortraitUrl(rawUrl)

          if (!isCurrent(revision, isLive)) {
            return
          }

          onDraftedRef.current?.(res.id)

          if (!resolved) {
            setDraft({ id: res.id, previewUrl: withDraft?.previewUrl ?? '' })
            setPreviewError(true)
            push({ role: 'system', text: getStrings().living.outfit.design.previewFailed, tone: 'info' })

            return
          }

          setDraft({ id: res.id, previewUrl: resolved })
          void saveCreationDraftImage('outfit', null)

          push({
            role: 'system',
            // 首次生成无「微调」可言（send 对无草稿请求强制 edit），按是否基于已有草稿区分文案。
            text:
              withDraft && mode === 'edit'
                ? getStrings().living.outfit.design.refined
                : getStrings().living.outfit.design.drafted,
            tone: 'info'
          })
        } catch (err) {
          if (!isCurrent(revision, isLive)) {
            return
          }

          const timedOut = err instanceof ApiError && err.code === 'timeout'
          const unknown = timedOut || ipcErrorStatus(err) === null || ipcErrorStatus(err) === 409
          setResultUnknown(unknown)

          // 保留本次输入与参考图；只有明确失败才提供直接重试。
          setLastRequest({ image, text, mode })
          push({
            role: 'system',
            text: unknown
              ? getStrings().living.outfit.design.timedOut
              : backendDetailMessage(err, getStrings().living.outfit.design.generateFailed),
            tone: unknown ? 'info' : 'error'
          })
          log.warn('wardrobe-design', timedOut ? 'generation result timed out' : 'generation failed', err)
        } finally {
          if (isCurrent(revision, isLive)) {
            generatingRef.current = false
            setBusy(false)
            setStage(null)
          }
        }
      })()
    },
    [begin, isCurrent, onDraftedRef, push]
  )

  const send = useCallback(
    (text: string, mode: ImageReviseMode = 'edit'): void => {
      const trimmed = text.trim()

      if (generatingRef.current || resultUnknown || previewError || (!draft && !trimmed && !refImage)) {
        return
      }

      const image = refImage
      setRefImage(null)

      runDesign(trimmed, image, draft, draft ? mode : 'edit')
    },
    [draft, previewError, refImage, resultUnknown, runDesign]
  )

  const retry = useCallback((): void => {
    if (generatingRef.current || resultUnknown || !lastRequest) {
      return
    }

    runDesign(lastRequest.text, lastRequest.image, draft, lastRequest.mode)
  }, [draft, lastRequest, resultUnknown, runDesign])

  const confirm = useCallback(async (): Promise<void> => {
    const isLive = begin()

    if (!isLive() || !draft?.previewUrl || generatingRef.current || resultUnknown || previewError) {
      return
    }

    const revision = ++revisionRef.current
    generatingRef.current = true
    setBusy(true)
    setStage('confirming')

    try {
      // 确认转正参考图，并由服务端安排基础动作；就绪后由用户选择穿上。
      await requestResource('companion.post_outfit_confirm', {
        path: { outfit_id: draft.id },
        body: {}
      })

      if (isLive()) {
        void hydrateWardrobe()
      }

      if (isCurrent(revision, isLive)) {
        setDraft(null)
        setMessages([])
        void saveCreationDraftImage('outfit', null)
        onConfirmed(draft.id)
      }
    } catch (err) {
      if (!isCurrent(revision, isLive)) {
        return
      }

      push({ role: 'system', text: backendDetailMessage(err, getStrings().living.outfit.confirmFailed), tone: 'error' })
      setResultUnknown(ipcErrorStatus(err) === null || ipcErrorStatus(err) === 409)
      log.warn('wardrobe-design', 'confirm failed', err)
    } finally {
      if (isCurrent(revision, isLive)) {
        generatingRef.current = false
        setBusy(false)
        setStage(null)
      }
    }
  }, [begin, draft, isCurrent, onConfirmed, previewError, push, resultUnknown])

  const attachRefImage = useCallback(async (): Promise<void> => {
    const isLive = begin()
    const revision = revisionRef.current
    const referenceRevision = ++referenceRevisionRef.current
    setRestoringReference(false)
    const picked = await pickAvatarImage(getStrings().living.outfit.design.pickReferenceTitle)

    if (!picked || !isCurrent(revision, isLive) || referenceRevision !== referenceRevisionRef.current) {
      return
    }

    if ('error' in picked) {
      push({ role: 'system', text: picked.error, tone: 'error' })

      return
    }

    setRefImage(picked.image)
    void saveCreationDraftImage('outfit', picked.image)
  }, [begin, isCurrent, push])

  const clearRefImage = useCallback((): void => {
    referenceRevisionRef.current += 1
    setRestoringReference(false)
    setRefImage(null)
    void saveCreationDraftImage('outfit', null)
  }, [])

  // 从列表里的既有草稿续上设计会话（微调 / 直接确认入柜）。
  const adoptDraft = useCallback(
    (id: number, previewUrl: string): void => {
      reset()
      msgIdRef.current += 1
      setMessages([
        {
          id: msgIdRef.current,
          role: 'system',
          text: previewUrl
            ? getStrings().living.outfit.design.resumeReady
            : getStrings().living.outfit.design.resumePreviewMissing,
          tone: 'info'
        }
      ])
      setDraft({ id, previewUrl })
    },
    [reset]
  )

  return {
    messages,
    draft,
    refImage,
    busy: busy || restoringReference,
    stage: restoringReference ? 'loading' : stage,
    resultUnknown,
    previewError,
    lastRequest,
    send,
    retry,
    confirm,
    attachRefImage,
    clearRefImage,
    adoptDraft,
    restoreReference,
    reset
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
  id: number,
  feedback: string,
  mode: ImageReviseMode
): Promise<ApiSchemas['OutfitResponse']> {
  return requestResource('companion.post_outfit_regenerate', {
    path: { outfit_id: id },
    body: { source: 'generate', feedback: feedback || undefined, mode }
  })
}
