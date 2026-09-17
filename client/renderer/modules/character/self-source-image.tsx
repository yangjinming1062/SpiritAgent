import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Check, Copy } from '@/shared/lib/icons'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, WizardModal } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { pickAvatarImage, type PickedImage } from './avatar-image'

interface SelfSourceImageFlowProps {
  open: boolean
  title: string
  /** 打开时拉取后端组装的自备图提示词；失败时给重试入口。 */
  fetchPrompt: () => Promise<string>
  /** 采纳选中的图片；成功后组件自行关闭。抛错时展示后端公开文案。 */
  adopt: (image: PickedImage) => Promise<void>
  /** 改用 AI 生成：关闭弹窗，由调用方走原生成入口。 */
  onUseAi: () => void
  onClose: () => void
}

// 自备图流程弹窗：复制后端下发的完整提示词 → 用户用任意外部工具生成 → 选图上传采纳。
// 组件只负责流程编排；提示词与采纳接口由各生成点位注入。回调经 ref 取用，
// 避免调用方未 memo 的内联函数在每次 render 都重触发提示词拉取。
export function SelfSourceImageFlow({
  open,
  title,
  fetchPrompt,
  adopt,
  onUseAi,
  onClose
}: SelfSourceImageFlowProps): React.JSX.Element | null {
  const t = useStrings().selfSource
  const [prompt, setPrompt] = useState<string | null>(null)
  const [promptError, setPromptError] = useState<string | null>(null)
  const [image, setImage] = useState<PickedImage | null>(null)
  const [picking, setPicking] = useState(false)
  const [adopting, setAdopting] = useState(false)
  const [adoptError, setAdoptError] = useState<string | null>(null)

  const fetchPromptRef = useRef(fetchPrompt)
  fetchPromptRef.current = fetchPrompt
  const adoptRef = useRef(adopt)
  adoptRef.current = adopt
  const promptAbortRef = useRef<(() => void) | null>(null)

  const loadPrompt = useCallback((): void => {
    setPrompt(null)
    setPromptError(null)

    let live = true

    void fetchPromptRef
      .current()
      .then((text: string): void => {
        if (live) {
          setPrompt(text)
        }
      })
      .catch((err: unknown): void => {
        // 提示词端点会返回可行动的守卫文案（如缺着装描述、缺正面种子）；解出后端 detail 就展示，解不出用通用文案。
        if (live) {
          setPromptError(backendDetailMessage(err, t.promptFailed))
        }
      })

    promptAbortRef.current = (): void => {
      live = false
    }
  }, [t.promptFailed])

  useEffect((): (() => void) => {
    if (!open) {
      return () => undefined
    }

    setImage(null)
    setPicking(false)
    setAdopting(false)
    setAdoptError(null)
    loadPrompt()

    return () => {
      promptAbortRef.current?.()
    }
  }, [open, loadPrompt])

  if (!open) {
    return null
  }

  const chooseImage = async (): Promise<void> => {
    setPicking(true)
    const result = await pickAvatarImage(t.pickTitle)

    if (result && 'image' in result) {
      setImage(result.image)
      setAdoptError(null)
    } else if (result && 'error' in result) {
      setAdoptError(result.error)
    }

    setPicking(false)
  }

  const confirmAdopt = async (): Promise<void> => {
    if (!image || adopting) {
      return
    }

    setAdopting(true)
    setAdoptError(null)

    try {
      await adoptRef.current(image)
      onClose()
    } catch (err) {
      // 回调直接透传 IPC 原始错误：解出后端 detail 里的公开文案，解不出用通用失败文案。
      setAdoptError(backendDetailMessage(err, t.adoptFailed))
    } finally {
      setAdopting(false)
    }
  }

  return (
    <WizardModal onClose={onClose} regionId="self-source-image" title={title}>
      <div className="space-y-3">
        <p className={HINT_TEXT}>{t.hint}</p>

        {promptError ? (
          <div className="space-y-2">
            <p className="text-xs text-danger-fg" role="alert">
              {promptError}
            </p>
            <button className={BTN_SUBTLE} onClick={loadPrompt} type="button">
              {t.retryPrompt}
            </button>
          </div>
        ) : prompt === null ? (
          <p className="rounded-xl border border-line-hairline bg-fill-trough px-4 py-6 text-center text-xs text-muted">
            {t.promptLoading}
          </p>
        ) : (
          <div className="relative rounded-xl border border-line-hairline bg-fill-trough p-3">
            <p className="max-h-48 overflow-y-auto whitespace-pre-wrap break-all pr-6 text-xs leading-relaxed text-body">
              {prompt}
            </p>
            <SelfSourceCopyButton text={prompt} />
          </div>
        )}

        <div className="flex items-center gap-2">
          {image && (
            <img
              alt={t.pickTitle}
              className="size-16 rounded-lg border border-line-hairline object-contain"
              src={image.previewUrl}
            />
          )}
          <button
            className={BTN_SUBTLE}
            disabled={picking || adopting}
            onClick={() => void chooseImage()}
            type="button"
          >
            {image ? t.replaceImage : t.pickImage}
          </button>
        </div>

        {adoptError && (
          <p className="text-xs text-danger-fg" role="alert">
            {adoptError}
          </p>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button className={BTN_SUBTLE} disabled={adopting} onClick={onUseAi} type="button">
            {t.useAi}
          </button>
          <button
            className={BTN_PRIMARY}
            disabled={!image || adopting}
            onClick={() => void confirmAdopt()}
            type="button"
          >
            {adopting ? t.adopting : t.adopt}
          </button>
        </div>
      </div>
    </WizardModal>
  )
}

function SelfSourceCopyButton({ text }: { text: string }): React.JSX.Element {
  const t = useStrings().selfSource
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect((): (() => void) => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
  }, [])

  const copy = async (): Promise<void> => {
    try {
      if (window.spiritagent?.writeClipboard) {
        await window.spiritagent.writeClipboard(text)
      } else {
        await navigator.clipboard.writeText(text)
      }

      setCopied(true)

      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }

      timerRef.current = setTimeout(() => {
        setCopied(false)
        timerRef.current = null
      }, 1500)
    } catch {
      /* 复制失败保持原态，用户可重试 */
    }
  }

  return (
    <button
      aria-label={copied ? t.copied : t.copyPrompt}
      className="absolute top-2 right-2 inline-flex size-6 items-center justify-center rounded-md text-muted transition select-none hover:bg-fill-hover/80 hover:text-strong"
      onClick={() => void copy()}
      title={copied ? t.copied : t.copyPrompt}
      type="button"
    >
      {copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}
    </button>
  )
}
