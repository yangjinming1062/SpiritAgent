import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { openCreation } from '@/app/navigation/app-navigation'
import { MediaPreview } from '@/features/media'
import { ArrowLeft, ImagePlus, Sparkles, Video } from '@/shared/lib/icons'
import { failureText, ipcErrorCode } from '@/shared/lib/ipc-error'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  CreationSteps,
  ErrorBanner,
  FIELD_LABEL,
  INPUT_CLASS
} from '@/ui'
import type { VisualInterpretation } from '@protocol'

import { composeDescribedInput } from '../../../../../shared/described-input'
import { isStaleDraftError } from '../../../../../shared/visuals'

import { visualsApi } from './api'
import { $visualsUi, beginStudioInterpretation, EMPTY_STUDIO, updateStudio } from './visuals-state'

type Intent = 'photo' | 'video' | 'motion'

const INTENT_ICONS: Record<Intent, typeof ImagePlus> = { photo: ImagePlus, video: Video, motion: Sparkles }

const VISUAL_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov'

const ACCEPTED_MIME = [
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
  'video/mp4',
  'video/webm',
  'video/quicktime'
]

/** 文件校验：先按 MIME，再按扩展名兜底（MOV 部分系统不回报 MIME）。 */
function acceptedFile(file: File): boolean {
  return ACCEPTED_MIME.includes(file.type) || /\.(png|jpe?g|webp|gif|mp4|webm|mov)$/i.test(file.name)
}

/**
 * 创建影像专注视图：描述 → 预览 → 确认；上传是独立入口，拖放或选文件后提交。
 * 客户端不解析草稿字段，只展示服务端给出的摘要与细节。
 */
export function VisualCreatePanel({
  onDone,
  onExit
}: {
  onDone: (visualId?: string) => Promise<void>
  onExit: () => void
}): React.JSX.Element {
  const strings = useStrings()
  const t = strings.studio
  const v = strings.visuals
  const ui = useStore($visualsUi)

  const studio = ui.studio ?? { ...EMPTY_STUDIO, mode: 'describe' as const }
  const uploadMode = studio.mode === 'upload'

  const preview = studio.preview as VisualInterpretation | null
  const [busy, setBusy] = useState<'interpret' | 'create' | 'upload' | null>(null)
  const [error, setError] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const patch = (next: Partial<typeof studio>): void => {
    updateStudio(next)
    setError('')
  }

  const interpret = async (): Promise<void> => {
    const description = composeDescribedInput(studio.description, studio.refinement)

    if (!description || busy) {
      return
    }

    // 结果只在请求代次与草稿修订都未变时落地：改描述、换类型或退出都会作废在途整理。
    const accept = beginStudioInterpretation()
    setBusy('interpret')
    setError('')

    try {
      const result = await visualsApi.interpret({ description, intent: studio.intent })

      if (!accept()) {
        return
      }

      patch({ preview: result, description, refinement: '' })
    } catch (failure) {
      // 过期失败不替换当前提示，也不清草稿。
      if (accept()) {
        setError(failureText(failure, t.interpretFailed))
      }
    } finally {
      setBusy(null)
    }
  }

  const confirm = async (): Promise<void> => {
    if (!preview || !preview.draft || busy) {
      return
    }

    setBusy('create')
    setError('')

    try {
      await visualsApi.create(preview.draft as Record<string, unknown>)
      patch({ preview: null, description: '', refinement: '', mode: 'idle', intent: 'photo' })
      await onDone()
    } catch (failure) {
      if (isStaleDraftError(ipcErrorCode(failure))) {
        patch({ preview: null })
        setError(t.invalidated)
      } else {
        setError(failureText(failure, t.createFailed))
      }
    } finally {
      setBusy(null)
    }
  }

  const upload = async (): Promise<void> => {
    if (!file || busy) {
      return
    }

    setBusy('upload')
    setError('')

    try {
      const data = new Uint8Array(await file.arrayBuffer())
      const result = await visualsApi.upload(data, { filename: file.name })
      setFile(null)

      if (fileInput.current) {
        fileInput.current.value = ''
      }

      patch({ mode: 'idle' })
      await onDone(result.id)
    } catch (failure) {
      setError(failureText(failure, t.createFailed))
    } finally {
      setBusy(null)
    }
  }

  const acceptFiles = (list: FileList | File[]): void => {
    const files = Array.from(list)

    if (files.length === 0) {
      return
    }

    if (files.length > 1) {
      setError(v.composer.multipleFiles)

      return
    }

    if (!acceptedFile(files[0])) {
      setError(v.composer.unsupportedFile)

      return
    }

    setFile(files[0])
    setError('')
  }

  const step = (): { label: string; section: 'actions' | 'wardrobe' | 'environment' } | null => {
    switch (preview?.next_step) {
      case 'create_action':
        return { label: t.stepAction, section: 'actions' }

      case 'create_outfit':
        return { label: t.stepOutfit, section: 'wardrobe' }

      case 'create_scene':
        return { label: t.stepScene, section: 'environment' }

      default:
        return null
    }
  }

  const next = step()
  const stepIndex = uploadMode ? (file ? 2 : 1) : preview ? 2 : 1

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button className={BTN_SUBTLE} onClick={onExit} type="button">
          <ArrowLeft aria-hidden="true" className="size-3.5" />
          <span>{v.backToList}</span>
        </button>
        <h2 className="text-[15px] font-semibold text-strong">{uploadMode ? v.composer.upload : v.composer.create}</h2>
      </div>

      <CreationSteps
        labels={
          uploadMode ? [t.steps.pick, t.steps.confirm, t.steps.done] : [t.steps.describe, t.steps.confirm, t.steps.done]
        }
        step={stepIndex}
      />

      {error && <ErrorBanner message={error} />}

      {!uploadMode ? (
        <>
          {!preview && (
            <>
              <CapsuleTabs
                ariaLabel={t.descriptionLabel}
                onChange={value => patch({ intent: value })}
                options={(['photo', 'video', 'motion'] as const).map(value => ({
                  value,
                  label: t.intents[value],
                  icon: INTENT_ICONS[value]
                }))}
                size="sm"
                value={studio.intent}
                variant="segmented"
              />
              <p className="text-[11px] leading-relaxed text-muted">{t.intents[`${studio.intent}Hint`]}</p>
              <label className="block">
                <span className={FIELD_LABEL}>{t.descriptionLabel}</span>
                <textarea
                  className={cn(INPUT_CLASS, 'min-h-24')}
                  onChange={event => patch({ description: event.currentTarget.value })}
                  placeholder={t.descriptionPlaceholder}
                  value={studio.description}
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <button
                  className={BTN_PRIMARY}
                  disabled={busy !== null || !studio.description.trim()}
                  onClick={() => void interpret()}
                  type="button"
                >
                  {busy === 'interpret' ? t.describing : t.describe}
                </button>
              </div>
            </>
          )}

          {preview && (
            <div className="space-y-3 rounded-xl border border-line-hairline bg-fill-faint/40 p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-muted">
                  {preview.feasible ? t.previewTitle : t.infeasible}
                </span>
                <button className={BTN_GHOST} onClick={() => patch({ preview: null })} type="button">
                  {t.editDescription}
                </button>
              </div>
              {preview.feasible ? (
                <>
                  <p className="text-sm font-semibold text-strong">{preview.summary}</p>
                  <dl className="grid gap-1 text-xs text-muted">
                    {preview.details.map(item => (
                      <div className="flex gap-2" key={`${item.label}:${item.value}`}>
                        <dt className="shrink-0 text-faint">{item.label}</dt>
                        <dd className="min-w-0 break-words">{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                  {preview.materials.some(material => material.preview_url) && (
                    <div className="flex gap-2">
                      {preview.materials
                        .filter(material => material.preview_url)
                        .map(material => (
                          <MediaPreview key={material.id} type="image" url={material.preview_url ?? ''}>
                            {state =>
                              state.status === 'ready' && state.src ? (
                                <img
                                  alt={material.name}
                                  className="size-16 rounded-lg border border-line-hairline object-cover"
                                  onError={state.onError}
                                  src={state.src}
                                />
                              ) : (
                                <span className="size-16 rounded-lg border border-line-hairline bg-fill-faint" />
                              )
                            }
                          </MediaPreview>
                        ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-muted">{preview.reason}</p>
                  {next && (
                    <button className={BTN_SUBTLE} onClick={() => void openCreation(next.section)} type="button">
                      {next.label}
                    </button>
                  )}
                </div>
              )}
              <label className="block">
                <span className={FIELD_LABEL}>{t.refineLabel}</span>
                <input
                  className={INPUT_CLASS}
                  onChange={event => patch({ refinement: event.currentTarget.value })}
                  placeholder={t.refinePlaceholder}
                  value={studio.refinement}
                />
              </label>
              <div className="flex gap-2">
                {preview.feasible && (
                  <button className={BTN_PRIMARY} disabled={busy !== null} onClick={() => void confirm()} type="button">
                    {busy === 'create' ? v.composer.creating : t.confirm}
                  </button>
                )}
                <button
                  className={preview.feasible ? BTN_SUBTLE : BTN_PRIMARY}
                  disabled={busy !== null}
                  onClick={() => void interpret()}
                  type="button"
                >
                  {busy === 'interpret' ? t.describing : t.refine}
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="text-[11px] leading-relaxed text-muted">{v.composer.uploadHint}</p>
          <div
            className={cn(
              'grid place-items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition',
              dragOver ? 'border-accent bg-accent-soft/20' : 'border-line-standard bg-fill-faint/40'
            )}
            onDragLeave={() => setDragOver(false)}
            onDragOver={event => {
              event.preventDefault()
              setDragOver(true)
            }}
            onDrop={event => {
              event.preventDefault()
              setDragOver(false)
              acceptFiles(event.dataTransfer.files)
            }}
          >
            <ImagePlus aria-hidden="true" className="size-6 text-faint" />
            <p className="text-xs font-medium text-strong">
              {dragOver ? v.composer.dropzoneActive : v.composer.dropzoneTitle}
            </p>
            <p className="text-[11px] text-muted">{v.composer.dropzoneHint}</p>
            <button className={BTN_SUBTLE} onClick={() => fileInput.current?.click()} type="button">
              {v.composer.browse}
            </button>
            <p className="text-[10px] text-faint">{v.composer.supportedFormats}</p>
            <input
              accept={VISUAL_ACCEPT}
              className="hidden"
              onChange={event => acceptFiles(event.currentTarget.files ?? [])}
              ref={fileInput}
              type="file"
            />
          </div>

          {file && (
            <div className="space-y-2 rounded-xl border border-line-hairline bg-fill-faint/40 p-3">
              <p className="break-all text-xs font-medium text-strong">{file.name}</p>
              <p className="text-[11px] leading-relaxed text-muted">{v.composer.fileReady}</p>
              <div className="flex flex-wrap gap-2">
                <button className={BTN_PRIMARY} disabled={busy !== null} onClick={() => void upload()} type="button">
                  {busy === 'upload' ? v.composer.uploading : v.composer.uploadSubmit}
                </button>
                <button className={BTN_SUBTLE} onClick={() => fileInput.current?.click()} type="button">
                  {v.composer.replaceFile}
                </button>
                <button
                  className={BTN_GHOST}
                  onClick={() => {
                    setFile(null)

                    if (fileInput.current) {
                      fileInput.current.value = ''
                    }
                  }}
                  type="button"
                >
                  {v.composer.removeFile}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
