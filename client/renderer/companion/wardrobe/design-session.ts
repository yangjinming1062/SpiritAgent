import { useCallback, useEffect, useRef, useState } from 'react'

import { pickAvatarImage, type PickedImage, resolvePortraitUrl } from '@/companion/avatar-image'
import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

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

// 后端 4xx 错误体形如 409 {"detail":{"error":"…"}}——剥掉状态码前缀解析 JSON，
// 取 detail 里的公开文案；解析不了就用兜底。
function outfitErrMsg(err: unknown, fallback: string): string {
  const raw = unwrapIpcErrorMessage(err).replace(/^\d{3}\s*/, '')

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
// 服装/发型可换、五官锁定——身份由后端用头像种子锚定，这里只收集着装意图。
export function useOutfitDesignSession(onConfirmed: () => void): {
  messages: DesignMessage[]
  draft: DesignDraft | null
  refImage: PickedImage | null
  busy: boolean
  send: (text: string) => void
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

  const mountedRef = useRef(true)
  const generatingRef = useRef(false)
  const msgIdRef = useRef(0)

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  const push = useCallback((message: Omit<DesignMessage, 'id'>): void => {
    if (!mountedRef.current) {
      return
    }

    msgIdRef.current += 1
    setMessages(prev => [...prev, { ...message, id: msgIdRef.current }])
  }, [])

  const send = useCallback(
    (text: string): void => {
      const trimmed = text.trim()

      if (generatingRef.current || (!draft && !trimmed && !refImage)) {
        return
      }

      generatingRef.current = true
      setBusy(true)

      push({ imageUrl: refImage?.previewUrl, role: 'user', text: trimmed || '（按参考图设计）' })

      const image = refImage
      setRefImage(null)

      void (async () => {
        try {
          // 有草稿后只走反馈微调（后端 regenerate 不收图）；参考图仅用于首次生成。
          const res = draft ? await runRegenerate(draft.id, trimmed) : await runCreate(trimmed, image)

          const rawUrl = res?.fullbody_url || null
          const resolved = rawUrl ? await resolvePortraitUrl(rawUrl) : null

          if (!res?.id || !rawUrl || !resolved) {
            throw new Error('invalid outfit response')
          }

          if (!mountedRef.current) {
            return
          }

          setDraft({ id: res.id, previewUrl: resolved })
          push({ role: 'system', text: '草稿已生成，见上方预览。继续描述可以微调重绘，满意就确认入柜。', tone: 'info' })
        } catch (err) {
          push({ role: 'system', text: outfitErrMsg(err, '外观生成失败，请稍后重试'), tone: 'error' })
          log.warn('wardrobe-design', 'generation failed', err)
        } finally {
          generatingRef.current = false

          if (mountedRef.current) {
            setBusy(false)
          }
        }
      })()
    },
    [draft, push, refImage]
  )

  const confirm = useCallback(async (): Promise<void> => {
    if (!draft || generatingRef.current) {
      return
    }

    generatingRef.current = true
    setBusy(true)

    try {
      await window.spiritagent.api({ path: `/api/companion/outfits/${draft.id}/confirm`, method: 'POST' })

      if (mountedRef.current) {
        setDraft(null)
        setMessages([])
        onConfirmed()
      }
    } catch (err) {
      push({ role: 'system', text: outfitErrMsg(err, '确认失败，请稍后重试'), tone: 'error' })
      log.warn('wardrobe-design', 'confirm failed', err)
    } finally {
      generatingRef.current = false

      if (mountedRef.current) {
        setBusy(false)
      }
    }
  }, [draft, onConfirmed, push])

  const attachRefImage = useCallback(async (): Promise<void> => {
    const picked = await pickAvatarImage('选择服装参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      push({ role: 'system', text: picked.error, tone: 'error' })

      return
    }

    setRefImage(picked.image)
  }, [push])

  const clearRefImage = useCallback((): void => {
    setRefImage(null)
  }, [])

  // 从列表里的既有草稿续上设计会话（微调 / 直接确认入柜）。
  const adoptDraft = useCallback((id: number, previewUrl: string): void => {
    msgIdRef.current += 1
    setMessages([{ id: msgIdRef.current, role: 'system', text: '继续微调这套草稿，或直接确认入柜。', tone: 'info' }])
    setDraft({ id, previewUrl })
  }, [])

  const reset = useCallback((): void => {
    setMessages([])
    setDraft(null)
    setRefImage(null)
  }, [])

  return {
    messages,
    draft,
    refImage,
    busy,
    send,
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
