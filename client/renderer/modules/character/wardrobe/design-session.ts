import { useCallback, useEffect, useRef, useState } from 'react'

import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'

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

// 主进程错误包含状态码、请求路径和 JSON 错误体，
// 取 detail 里的公开文案；解析不了就用兜底。
function outfitErrMsg(err: unknown, fallback: string): string {
  const raw = unwrapIpcErrorMessage(err).replace(/^\d{3}\s+(?:\/[^\s]*:\s*)?/, '')

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

// 衣柜页的设计会话：着装描述 + 可选参考图 → 草稿 → 反馈微调重绘 → 确认入柜并自动穿着。
// 服装/发型可换、五官锁定——身份与身材由后端用全身种子图锚定，这里只收集着装意图。
// 发出失败的请求保留在 lastRequest 里供一键重试，避免用户重打描述、重传参考图。
export function useOutfitDesignSession(onConfirmed: () => void): {
  messages: DesignMessage[]
  draft: DesignDraft | null
  refImage: PickedImage | null
  busy: boolean
  lastRequest: { image: PickedImage | null; text: string } | null
  send: (text: string) => void
  retry: () => void
  confirm: () => Promise<void>
  attachRefImage: () => Promise<void>
  clearRefImage: () => void
  adoptDraft: (id: number, previewUrl: string) => void
  reset: () => void
} {
  const [messages, setMessages] = useState<DesignMessage[]>([])
  const [draft, setDraft] = useState<DesignDraft | null>(null)
  const [refImage, setRefImage] = useState<PickedImage | null>(null)
  const [busy, setBusy] = useState(false)
  const [lastRequest, setLastRequest] = useState<{ image: PickedImage | null; text: string } | null>(null)

  const mountedRef = useRef(true)
  const generatingRef = useRef(false)
  const msgIdRef = useRef(0)
  const revisionRef = useRef(0)

  const reset = useCallback((): void => {
    revisionRef.current += 1
    generatingRef.current = false
    setBusy(false)
    setMessages([])
    setDraft(null)
    setRefImage(null)
    setLastRequest(null)
  }, [])

  useEffect(() => {
    mountedRef.current = true
    const unregister = registerStorageClearHandler(reset)

    return () => {
      unregister()
      mountedRef.current = false
      revisionRef.current += 1
    }
  }, [reset])

  const isCurrent = useCallback((revision: number, epoch: number): boolean => {
    return mountedRef.current && revision === revisionRef.current && epoch === currentClearEpoch()
  }, [])

  const push = useCallback((message: Omit<DesignMessage, 'id'>): void => {
    if (!mountedRef.current) {
      return
    }

    msgIdRef.current += 1
    setMessages(prev => [...prev, { ...message, id: msgIdRef.current }])
  }, [])

  const runDesign = useCallback(
    (text: string, image: PickedImage | null, withDraft: DesignDraft | null): void => {
      if (!mountedRef.current || generatingRef.current) {
        return
      }

      const revision = ++revisionRef.current
      const epoch = currentClearEpoch()
      generatingRef.current = true
      setBusy(true)
      setLastRequest(null)

      push({ imageUrl: image?.previewUrl, role: 'user', text: text || '（按参考图设计）' })

      void (async () => {
        try {
          // 有草稿后只走反馈微调（后端 regenerate 不收图）；参考图仅用于首次生成。
          const res = withDraft ? await runRegenerate(withDraft.id, text) : await runCreate(text, image)

          if (!isCurrent(revision, epoch)) {
            return
          }

          const rawUrl = res?.fullbody_url || null

          if (!res?.id || !rawUrl) {
            throw new Error('invalid outfit response')
          }

          const resolved = await resolvePortraitUrl(rawUrl)

          if (!isCurrent(revision, epoch)) {
            return
          }

          void hydrateWardrobe()

          if (!resolved) {
            setDraft({ id: res.id, previewUrl: '' })
            push({ role: 'system', text: '草稿已生成，但预览加载失败，请从衣橱重新打开草稿。', tone: 'info' })

            return
          }

          setDraft({ id: res.id, previewUrl: resolved })
          push({ role: 'system', text: '草稿已生成，见上方预览。继续描述可以微调重绘，满意就确认入柜。', tone: 'info' })
        } catch (err) {
          if (!isCurrent(revision, epoch)) {
            return
          }

          const rawError = unwrapIpcErrorMessage(err)

          const timedOut =
            (err instanceof Error && err.name === 'TimeoutError') ||
            /^(?:Error invoking remote method '[^']+': )?TimeoutError:|^The operation was aborted due to timeout$/.test(
              rawError
            )

          if (withDraft && (timedOut || /^409 /.test(rawError))) {
            setDraft({ id: withDraft.id, previewUrl: '' })
          }

          // 失败后保留本次输入与参考图，失败气泡旁给一键重试，不必重打描述或重传图。
          setLastRequest({ image, text })
          push({
            role: 'system',
            text: timedOut
              ? '等待结果超时，生成可能仍在继续。请稍后查看衣橱中的草稿，确认结果后再重试。'
              : outfitErrMsg(err, '外观生成失败，请稍后重试'),
            tone: timedOut ? 'info' : 'error'
          })
          log.warn('wardrobe-design', timedOut ? 'generation result timed out' : 'generation failed', err)
        } finally {
          if (isCurrent(revision, epoch)) {
            generatingRef.current = false
            setBusy(false)
          }
        }
      })()
    },
    [isCurrent, push]
  )

  const send = useCallback(
    (text: string): void => {
      const trimmed = text.trim()

      if (!mountedRef.current || generatingRef.current || (!draft && !trimmed && !refImage)) {
        return
      }

      const image = refImage
      setRefImage(null)

      runDesign(trimmed, image, draft)
    },
    [draft, refImage, runDesign]
  )

  const retry = useCallback((): void => {
    if (!mountedRef.current || generatingRef.current || !lastRequest) {
      return
    }

    runDesign(lastRequest.text, lastRequest.image, draft)
  }, [draft, lastRequest, runDesign])

  const confirm = useCallback(async (): Promise<void> => {
    if (!mountedRef.current || !draft?.previewUrl || generatingRef.current) {
      return
    }

    const revision = ++revisionRef.current
    const epoch = currentClearEpoch()
    generatingRef.current = true
    setBusy(true)

    try {
      await window.spiritagent.api({ path: `/api/companion/outfits/${draft.id}/confirm`, method: 'POST' })

      if (isCurrent(revision, epoch)) {
        setDraft(null)
        setMessages([])
        onConfirmed()
      }
    } catch (err) {
      if (!isCurrent(revision, epoch)) {
        return
      }

      push({ role: 'system', text: outfitErrMsg(err, '确认失败，请稍后重试'), tone: 'error' })
      log.warn('wardrobe-design', 'confirm failed', err)
    } finally {
      if (isCurrent(revision, epoch)) {
        generatingRef.current = false
        setBusy(false)
      }
    }
  }, [draft, isCurrent, onConfirmed, push])

  const attachRefImage = useCallback(async (): Promise<void> => {
    const revision = revisionRef.current
    const epoch = currentClearEpoch()
    const picked = await pickAvatarImage('选择服装参考图')

    if (!picked || !isCurrent(revision, epoch)) {
      return
    }

    if ('error' in picked) {
      push({ role: 'system', text: picked.error, tone: 'error' })

      return
    }

    setRefImage(picked.image)
  }, [isCurrent, push])

  const clearRefImage = useCallback((): void => {
    setRefImage(null)
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
            ? '继续微调这套草稿，或直接确认入柜。'
            : '草稿预览尚未加载，可继续描述微调，或稍后从衣橱重新打开。',
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
    busy,
    lastRequest,
    send,
    retry,
    confirm,
    attachRefImage,
    clearRefImage,
    adoptDraft,
    reset
  }
}

async function runCreate(
  description: string,
  image: PickedImage | null
): Promise<{ id?: number; fullbody_url?: string }> {
  return window.spiritagent.api<{ id?: number; fullbody_url?: string }>({
    path: '/api/companion/outfits',
    method: 'POST',
    body: {
      description: description || undefined,
      image: image?.base64,
      content_type: image?.contentType
    }
  })
}

async function runRegenerate(id: number, feedback: string): Promise<{ id?: number; fullbody_url?: string }> {
  return window.spiritagent.api<{ id?: number; fullbody_url?: string }>({
    path: `/api/companion/outfits/${id}/regenerate`,
    method: 'POST',
    body: { feedback: feedback || undefined }
  })
}
