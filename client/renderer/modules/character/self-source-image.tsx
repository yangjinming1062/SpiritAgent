import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { PortraitLightbox } from '@/shared'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useClipboard } from '@/shared/hooks/use-clipboard'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { useImageActions } from '@/shared/hooks/use-image-actions'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { Check, Copy, Download } from '@/shared/lib/icons'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, WizardModal } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { pickAvatarImage, type PickedImage } from './avatar-image'

/** 外部工具按提示词生图时需一并提供的种子参考图；label 说明图的用途（如「全身形象」）。 */
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

  const {
    copied: refCopied,
    copy: copyRefImage,
    error: refActionError,
    reset: resetRefActions,
    save: saveRefImage
  } = useImageActions()

  const begin = useAsyncGuard()
  const fetchPromptRef = useLatestRef(fetchPrompt)
  const adoptRef = useLatestRef(adopt)
  const promptLoadRef = useRef<Promise<void> | null>(null)
  const flowVersionRef = useRef(0)

  // 一次操作只在发起时的打开周期内有效：关闭、重新打开、卸载或换号后判活为 false。
  const beginOperation = useCallback((): (() => boolean) => {
    const isLive = begin()
    const version = flowVersionRef.current

    return () => isLive() && version === flowVersionRef.current
  }, [begin])

  const loadPrompt = useCallback((): void => {
    if (promptLoadRef.current) {
      return
    }

    setPrompt(null)
    setPromptError(null)

    const isCurrent = beginOperation()

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
  }, [beginOperation, fetchPromptRef, t.promptFailed])

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
    resetRefActions()
    setShowGuidance(false)
    setPrompt(null)
    setPromptError(null)

    return () => {
      flowVersionRef.current += 1
    }
  }, [open, resetRefActions])

  if (!open) {
    return null
  }

  const chooseImage = async (): Promise<void> => {
    const isCurrent = beginOperation()
    setPicking(true)
    const result = await pickAvatarImage(t.pickTitle)

    if (!isCurrent()) {
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

  const confirmAdopt = async (): Promise<void> => {
    if (!image || adopting) {
      return
    }

    setAdopting(true)
    setAdoptError(null)
    const isCurrent = beginOperation()

    try {
      // 场景提示词会准备待上传记录；已请求时先等它收敛，避免迟到记录取代采纳结果。
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
                          onClick={() => void copyRefImage(ref.url)}
                          title={t.copyRefImage}
                          type="button"
                        >
                          <Copy className="size-3.5" />
                        </button>
                        <button
                          aria-label={t.saveRefImage}
                          className="inline-flex size-6 items-center justify-center rounded-md bg-black/70 text-white/90 transition hover:bg-black/90 hover:text-white"
                          onClick={() => void saveRefImage(ref.url, ref.label)}
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
                    {refActionError === 'copy' ? t.copyRefImageFailed : t.saveRefImageFailed}
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
  const { status, copy } = useClipboard()
  const copied = status === 'copied'

  const onCopy = async (): Promise<void> => {
    try {
      await copy(text)
    } catch {
      /* 复制失败保持原态，用户可重试 */
    }
  }

  return (
    <button
      aria-label={copied ? t.copied : t.copyPrompt}
      className="absolute top-2 right-2 inline-flex size-6 items-center justify-center rounded-md text-muted transition select-none hover:bg-fill-hover/80 hover:text-strong"
      onClick={() => void onCopy()}
      title={copied ? t.copied : t.copyPrompt}
      type="button"
    >
      {copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}
    </button>
  )
}
