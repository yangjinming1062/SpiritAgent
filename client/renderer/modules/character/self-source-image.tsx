import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { PortraitLightbox } from '@/shared'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { Check, Copy, Download } from '@/shared/lib/icons'
import { imageUrlForNativeClipboard } from '@/shared/lib/image-clipboard'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { currentClearEpoch } from '@/shared/lib/storage'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, WizardModal } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { pickAvatarImage, type PickedImage } from './avatar-image'

/** 外部工具按提示词生图时需一并提供的种子参考图；label 说明图的用途（如「全身种子图」）。 */
export interface SelfSourceReferenceImage {
  label: string
  url: string
}

interface SelfSourceImageFlowProps {
  open: boolean
  title: string
  /** 外部制作时使用的参考图。 */
  referenceImages?: SelfSourceReferenceImage[]
  referenceRequired?: boolean
  /** 当前生成点位的补充要求。 */
  hint?: string
  /** 按需拉取后端组装的自备图提示词；失败时给重试入口。 */
  fetchPrompt: () => Promise<string>
  /** 采纳选中的图片；成功后组件自行关闭。抛错时展示后端公开文案。 */
  adopt: (image: PickedImage) => Promise<void>
  /** 改用 AI 生成：关闭弹窗，由调用方走原生成入口。 */
  onUseAi: () => void
  onClose: () => void
}

// 各点位注入制作资料与采纳接口，组件负责选图、预览和提交。
export function SelfSourceImageFlow({
  open,
  title,
  referenceImages,
  referenceRequired = true,
  hint,
  fetchPrompt,
  adopt,
  onUseAi,
  onClose
}: SelfSourceImageFlowProps): React.JSX.Element | null {
  const t = useStrings().selfSource
  const [showGuidance, setShowGuidance] = useState(false)
  const [prompt, setPrompt] = useState<string | null>(null)
  const [promptError, setPromptError] = useState<string | null>(null)
  const [image, setImage] = useState<PickedImage | null>(null)
  const [picking, setPicking] = useState(false)
  const [adopting, setAdopting] = useState(false)
  const [adoptError, setAdoptError] = useState<string | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [refActionError, setRefActionError] = useState<string | null>(null)
  const [refCopied, setRefCopied] = useState(false)

  const fetchPromptRef = useRef(fetchPrompt)
  fetchPromptRef.current = fetchPrompt
  const adoptRef = useRef(adopt)
  adoptRef.current = adopt
  const promptLoadRef = useRef<Promise<void> | null>(null)
  const flowVersionRef = useRef(0)

  const loadPrompt = useCallback((): void => {
    if (promptLoadRef.current) {
      return
    }

    setPrompt(null)
    setPromptError(null)

    const version = flowVersionRef.current
    const epoch = currentClearEpoch()
    const isCurrent = (): boolean => version === flowVersionRef.current && epoch === currentClearEpoch()

    const request = fetchPromptRef
      .current()
      .then((text: string): void => {
        if (isCurrent()) {
          setPrompt(text)
        }
      })
      .catch((err: unknown): void => {
        if (isCurrent()) {
          setPromptError(backendDetailMessage(err, t.promptFailed))
        }
      })
      .finally(() => {
        if (promptLoadRef.current === request) {
          promptLoadRef.current = null
        }
      })

    promptLoadRef.current = request
  }, [t.promptFailed])

  // 灯箱打开时先关灯箱；WizardModal 同步停用 Esc，避免一次按键关掉整个自备图流程。
  useEscapeKey(() => setZoomUrl(null), { enabled: open && zoomUrl !== null })

  useEffect((): (() => void) => {
    flowVersionRef.current += 1
    promptLoadRef.current = null

    if (!open) {
      return () => undefined
    }

    setImage(null)
    setPicking(false)
    setAdopting(false)
    setAdoptError(null)
    setZoomUrl(null)
    setRefActionError(null)
    setRefCopied(false)
    setShowGuidance(false)
    setPrompt(null)
    setPromptError(null)

    return () => {
      flowVersionRef.current += 1
    }
  }, [open])

  if (!open) {
    return null
  }

  const chooseImage = async (): Promise<void> => {
    const version = flowVersionRef.current
    const epoch = currentClearEpoch()
    setPicking(true)
    const result = await pickAvatarImage(t.pickTitle)

    if (version !== flowVersionRef.current || epoch !== currentClearEpoch()) {
      return
    }

    if (result && 'image' in result) {
      setImage(result.image)
      setAdoptError(null)
    } else if (result && 'error' in result) {
      setAdoptError(result.error)
    }

    setPicking(false)
  }

  // 非 PNG/JPEG 的 data URL 先转 PNG，见 image-clipboard
  const copyReferenceImage = async (url: string): Promise<void> => {
    setRefActionError(null)
    setRefCopied(false)

    try {
      if (!window.spiritagent?.copyImage) {
        throw new Error('copyImage IPC unavailable')
      }

      await window.spiritagent.copyImage({ url: await imageUrlForNativeClipboard(url) })
      setRefCopied(true)
    } catch {
      setRefActionError(t.copyRefImageFailed)
    }
  }

  const saveReferenceImage = async (url: string, label: string): Promise<void> => {
    setRefActionError(null)

    try {
      if (!window.spiritagent?.saveImage) {
        throw new Error('saveImage IPC unavailable')
      }

      await window.spiritagent.saveImage({ defaultName: label || undefined, url })
    } catch {
      setRefActionError(t.saveRefImageFailed)
    }
  }

  const confirmAdopt = async (): Promise<void> => {
    if (!image || adopting) {
      return
    }

    setAdopting(true)
    setAdoptError(null)
    const version = flowVersionRef.current
    const epoch = currentClearEpoch()
    const isCurrent = (): boolean => version === flowVersionRef.current && epoch === currentClearEpoch()

    try {
      // 房间提示词会准备待上传记录；已请求时先等它收敛，避免迟到记录取代采纳结果。
      await promptLoadRef.current

      if (!isCurrent()) {
        return
      }

      await adoptRef.current(image)

      if (isCurrent()) {
        onClose()
      }
    } catch (err) {
      if (isCurrent()) {
        setAdoptError(backendDetailMessage(err, t.adoptFailed))
      }
    } finally {
      if (isCurrent()) {
        setAdopting(false)
      }
    }
  }

  return (
    <WizardModal
      escClose={!zoomUrl && !adopting}
      onClose={adopting ? () => undefined : onClose}
      regionId="self-source-image"
      title={title}
    >
      <div className="space-y-3">
        <p className={HINT_TEXT}>{t.hint}</p>
        {hint && <p className={HINT_TEXT}>{hint}</p>}

        <div className="flex items-center gap-2">
          {image && (
            <button className="cursor-zoom-in" onClick={() => setZoomUrl(image.previewUrl)} type="button">
              <img
                alt={t.pickTitle}
                className="size-24 rounded-lg border border-line-hairline object-contain"
                src={image.previewUrl}
              />
            </button>
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

        <button
          aria-expanded={showGuidance}
          className={BTN_SUBTLE}
          disabled={adopting}
          onClick={() => {
            setShowGuidance(value => !value)

            if (!showGuidance && prompt === null && !promptError) {
              loadPrompt()
            }
          }}
          type="button"
        >
          {t.guidanceAction}
        </button>
        {showGuidance && (
          <div className="space-y-3">
            {referenceImages && referenceImages.length > 0 ? (
              <div className="rounded-xl border border-line-hairline bg-fill-trough p-3">
                <p className="text-[11px] font-medium text-strong">{t.referenceTitle}</p>
                <p className="mt-0.5 text-[10px] leading-relaxed text-muted">{t.referenceHint}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {referenceImages.map(ref => (
                    <div
                      className="group relative overflow-hidden rounded-lg border border-line-hairline bg-surface-card"
                      key={ref.url}
                    >
                      <button
                        className="block cursor-zoom-in"
                        onClick={() => setZoomUrl(ref.url)}
                        title={ref.label}
                        type="button"
                      >
                        <img alt={ref.label} className="size-24 object-contain" draggable src={ref.url} />
                      </button>
                      <span className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 py-0.5 text-[9.5px] text-white">
                        {ref.label}
                      </span>
                      <div className="absolute right-0.5 top-0.5 flex gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100">
                        <button
                          aria-label={t.copyRefImage}
                          className="inline-flex size-6 items-center justify-center rounded-md bg-black/70 text-white/90 transition hover:bg-black/90 hover:text-white"
                          onClick={() => void copyReferenceImage(ref.url)}
                          title={t.copyRefImage}
                          type="button"
                        >
                          <Copy className="size-3.5" />
                        </button>
                        <button
                          aria-label={t.saveRefImage}
                          className="inline-flex size-6 items-center justify-center rounded-md bg-black/70 text-white/90 transition hover:bg-black/90 hover:text-white"
                          onClick={() => void saveReferenceImage(ref.url, ref.label)}
                          title={t.saveRefImage}
                          type="button"
                        >
                          <Download className="size-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {refCopied && (
                  <p className="mt-2 text-xs text-muted" role="status">
                    {t.copiedRefImage}
                  </p>
                )}
                {refActionError && (
                  <p className="mt-2 text-xs text-danger-fg" role="alert">
                    {refActionError}
                  </p>
                )}
              </div>
            ) : referenceRequired && prompt !== null ? (
              <p className="text-xs text-danger-fg" role="alert">
                {t.referenceMissing}
              </p>
            ) : null}

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
          </div>
        )}

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

      {zoomUrl && <PortraitLightbox name={t.referenceZoom} onClose={() => setZoomUrl(null)} url={zoomUrl} />}
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
