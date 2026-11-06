import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { openCreation } from '@/app/navigation/app-navigation'
import { MediaPreview } from '@/features/media'
import { ImagePlus, Sparkles, Video } from '@/shared/lib/icons'
import { failureText, ipcErrorCode } from '@/shared/lib/ipc-error'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, ErrorBanner, FIELD_LABEL, INPUT_CLASS, SettingCard } from '@/ui'
import type { VisualInterpretation } from '@protocol'

import { composeDescribedInput } from '../../../../../shared/described-input'
import { isStaleDraftError } from '../../../../../shared/visuals'

import { visualsApi } from './api'
import { $visualsUi, beginStudioInterpretation, EMPTY_STUDIO, updateStudio } from './visuals-state'

type Intent = 'photo' | 'video' | 'motion'

const INTENT_ICONS: Record<Intent, typeof ImagePlus> = { photo: ImagePlus, video: Video, motion: Sparkles }

const VISUAL_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov'

/**
 * 创作面板：描述 → 预览 → 确认。客户端不解析草稿字段，只展示服务端给出的摘要与细节。
 * 上传入口独立保留，不经过描述整理。
 */
export function VisualCreatePanel({ onDone }: { onDone: (visualId?: string) => Promise<void> }): React.JSX.Element {
  const strings = useStrings()
  const t = strings.studio
  const ui = useStore($visualsUi)

  const studio = ui.studio ?? { ...EMPTY_STUDIO, mode: 'describe' as const }

  const preview = studio.preview as VisualInterpretation | null
  const [busy, setBusy] = useState<'interpret' | 'create' | 'upload' | null>(null)
  const [error, setError] = useState('')
  const [file, setFile] = useState<File | null>(null)
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

    // 结果只在请求代次与草稿修订都未变时落地：改描述、换类型或关面板都会作废在途整理。
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

      await onDone(result.id)
    } catch (failure) {
      setError(failureText(failure, t.createFailed))
    } finally {
      setBusy(null)
    }
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

  return (
    <div className="space-y-3">
      {error && <ErrorBanner message={error} />}
      {!preview && (
        <div className="grid gap-2 sm:grid-cols-3">
          {(['photo', 'video', 'motion'] as const).map(value => {
            const Icon = INTENT_ICONS[value]
            const selected = studio.intent === value

            return (
              <button
                aria-pressed={selected}
                className={cn(
                  'flex items-start gap-2.5 rounded-xl border p-3 text-left transition',
                  selected
                    ? 'border-accent-line bg-accent-soft/40 text-strong'
                    : 'border-line-standard bg-fill-faint text-body hover:bg-fill-hover'
                )}
                key={value}
                onClick={() => patch({ intent: value })}
                type="button"
              >
                <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
                <span className="min-w-0">
                  <span className="block text-xs font-semibold">{t.intents[value]}</span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-muted">
                    {t.intents[`${value}Hint`]}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      )}

      {!preview && (
        <label className="block">
          <span className={FIELD_LABEL}>{t.descriptionLabel}</span>
          <textarea
            className={cn(INPUT_CLASS, 'min-h-20')}
            onChange={event => patch({ description: event.currentTarget.value })}
            placeholder={t.descriptionPlaceholder}
            value={studio.description}
          />
        </label>
      )}

      {preview && (
        <SettingCard divided={false}>
          <div className="flex flex-col gap-2 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-muted">{t.previewTitle}</span>
              <button className={BTN_GHOST} onClick={() => patch({ preview: null })} type="button">
                {t.refine}
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
                <div className="flex gap-2">
                  <button className={BTN_PRIMARY} disabled={busy !== null} onClick={() => void confirm()} type="button">
                    {busy === 'create' ? strings.common.processing : t.confirm}
                  </button>
                </div>
              </>
            ) : (
              <div className="space-y-2">
                <p className="text-sm font-medium text-strong">{t.infeasible}</p>
                <p className="text-xs text-muted">{preview.reason}</p>
                {next && (
                  <button className={BTN_SUBTLE} onClick={() => void openCreation(next.section)} type="button">
                    {next.label}
                  </button>
                )}
              </div>
            )}
          </div>
        </SettingCard>
      )}

      {!preview && (
        <div className="flex flex-wrap gap-2">
          <button
            className={BTN_PRIMARY}
            disabled={busy !== null || !studio.description.trim()}
            onClick={() => void interpret()}
            type="button"
          >
            {busy === 'interpret' ? t.describing : t.describe}
          </button>
          <label className="flex items-center gap-1.5">
            <input
              accept={VISUAL_ACCEPT}
              className="hidden"
              onChange={event => setFile(event.currentTarget.files?.[0] ?? null)}
              ref={fileInput}
              type="file"
            />
            <button className={BTN_SUBTLE} onClick={() => fileInput.current?.click()} type="button">
              {strings.visuals.composer.upload}
            </button>
            {file && <span className="text-[11px] text-muted">{file.name}</span>}
          </label>
          {file && (
            <button className={BTN_PRIMARY} disabled={busy !== null} onClick={() => void upload()} type="button">
              {busy === 'upload' ? strings.common.processing : t.confirm}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
