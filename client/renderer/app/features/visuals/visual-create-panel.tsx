import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { beginConversationInputActivity } from '@/modules/conversation'
import { visualsApi } from '@/modules/visuals'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { CheckCircle2, FileImage, FolderOpen, ImagePlus, Sparkles, Video, X } from '@/shared/lib/icons'
import { failureText } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, FIELD_LABEL, INPUT_CLASS, PanelSelect, SettingCard } from '@/shared/panel'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import {
  emptyVisualCreation,
  loadVisualMaterials,
  type VisualCreationDraft,
  visualCreationReady,
  type VisualMaterialOptions
} from '../../../../../shared/visuals'

import { $visualsUi, updateVisualsUi } from './visuals-state'

const VISUAL_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov'
const IMAGE_EXTENSIONS = /\.(png|jpe?g|webp|gif)$/i
const VIDEO_EXTENSIONS = /\.(mp4|mov|webm)$/i
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])
const VIDEO_TYPES = new Set(['video/mp4', 'video/webm', 'video/quicktime'])

function isVideoFile(file: File): boolean {
  return VIDEO_TYPES.has(file.type) || VIDEO_EXTENSIONS.test(file.name)
}

function isSupportedVisualFile(file: File): boolean {
  return (
    IMAGE_TYPES.has(file.type) ||
    VIDEO_TYPES.has(file.type) ||
    IMAGE_EXTENSIONS.test(file.name) ||
    VIDEO_EXTENSIONS.test(file.name)
  )
}

function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`
  }

  const units = ['KB', 'MB', 'GB']
  let value = size / 1024
  let unit = 0

  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }

  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`
}

export function VisualCreatePanel({ onDone }: { onDone: (visualId?: string) => Promise<void> }): React.JSX.Element {
  const strings = useStrings()
  const t = strings.visuals
  const c = t.composer
  const ui = useStore($visualsUi)
  const draft = ui.creation ?? emptyVisualCreation
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fileError, setFileError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const inputRef = useRef<HTMLInputElement>(null)
  const begin = useAsyncGuard()
  const upload = draft.mode === 'upload'

  useEffect(() => {
    if (draft.mode !== 'create') {
      return
    }

    const live = begin()
    void loadVisualMaterials({
      outfits: () => requestResource('companion.get_outfits'),
      scenes: cursor => requestResource('companion_scenes.scene_list_route', { query: { cursor, limit: 100 } }),
      actions: cursor =>
        requestResource('companion_actions.list_actions', { query: { cursor, limit: 100, group: 'in_library' } })
    })
      .then(value => {
        if (live()) {
          setMaterials(value)
        }
      })
      .catch(failure => {
        if (live()) {
          setError(failureText(failure, c.failed))
        }
      })
  }, [draft.mode, begin, c.failed])

  const patch = (value: Partial<VisualCreationDraft>): void => {
    updateVisualsUi({ creation: { ...draft, ...value } })
    setError('')
  }

  useEffect(() => {
    if (!file) {
      setPreview('')

      return
    }

    const url = URL.createObjectURL(file)
    setPreview(url)

    return () => {
      URL.revokeObjectURL(url)
    }
  }, [file])

  const selectFile = (next: File): void => {
    if (!isSupportedVisualFile(next)) {
      setFile(null)
      setFileError(c.unsupportedFile)

      return
    }

    setFile(next)
    setFileError('')
    setError('')
  }

  const removeFile = (): void => {
    setFile(null)
    setFileError('')

    if (inputRef.current) {
      inputRef.current.value = ''
    }
  }

  const openPicker = (): void => {
    if (!busy) {
      inputRef.current?.click()
    }
  }

  const handleDrop = (event: React.DragEvent<HTMLButtonElement>): void => {
    event.preventDefault()
    setDragging(false)

    if (busy) {
      return
    }

    const files = Array.from(event.dataTransfer.files)

    if (files.length !== 1) {
      setFileError(files.length > 1 ? c.multipleFiles : c.chooseFile)

      return
    }

    selectFile(files[0])
  }

  const submit = async (): Promise<void> => {
    if (busy || (upload ? !file : !visualCreationReady(draft))) {
      return
    }

    const live = begin()
    const finish = beginConversationInputActivity('uploading')
    setBusy(true)
    setError('')

    try {
      let visualId: string | undefined

      if (upload && file) {
        const data = new Uint8Array(await file.arrayBuffer())

        if (!live()) {
          return
        }

        const result = await visualsApi.upload(data, {
          filename: file.name
        })

        visualId = result.id
      } else {
        const requirements = draft.requirements.trim()

        if (draft.assetKind === 'actor') {
          await visualsApi.createActorClip({ outfit_id: draft.outfitId, action_id: draft.actionId, requirements })
        } else if (draft.assetKind === 'scene') {
          await visualsApi.createSceneMedia({ scene_id: draft.sceneId, requirements })
        } else {
          await visualsApi.createCinematic({
            outfit_id: draft.outfitId,
            scene_id: draft.sceneId,
            action_id: draft.actionId,
            media_kind: draft.mediaKind,
            requirements
          })
        }
      }

      if (live()) {
        removeFile()
        patch({ mode: null })

        if (!upload) {
          notify({ message: c.done })
        }

        await onDone(visualId)
      }
    } catch (failure) {
      if (live()) {
        setError(failureText(failure, c.failed))
      }
    } finally {
      finish()

      if (live()) {
        setBusy(false)
      }
    }
  }

  const materialSelect = (
    label: string,
    key: 'outfitId' | 'sceneId' | 'actionId',
    options: VisualMaterialOptions['outfits']
  ): React.JSX.Element => (
    <label className="block">
      <span className={FIELD_LABEL}>{label}</span>
      <PanelSelect
        ariaLabel={label}
        disabled={busy}
        onChange={value => patch({ [key]: value })}
        options={[{ value: '', label: c.select }, ...options.map(option => ({ value: option.id, label: option.name }))]}
        value={draft[key]}
        widthClass="w-full"
      />
    </label>
  )

  const actionCard = (
    mode: 'create' | 'upload',
    Icon: typeof ImagePlus,
    title: string,
    description: string
  ): React.JSX.Element => {
    const selected = draft.mode === mode

    return (
      <button
        aria-pressed={selected}
        className={cn(
          'group flex min-h-[88px] flex-1 items-start gap-3 rounded-2xl border p-4 text-left transition',
          'focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2',
          selected
            ? 'border-accent-line bg-accent-soft/40 text-strong shadow-sm'
            : 'border-line-standard bg-fill-faint text-body hover:border-line-strong hover:bg-fill-hover'
        )}
        disabled={busy}
        onClick={() => patch({ mode })}
        type="button"
      >
        <span
          className={cn(
            'grid size-10 shrink-0 place-items-center rounded-xl transition',
            selected ? 'bg-accent text-on-accent' : 'bg-fill-hover text-accent group-hover:bg-accent-soft'
          )}
        >
          <Icon aria-hidden="true" className="size-5" />
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold">{title}</span>
          <span className="mt-1 block text-[11px] leading-relaxed text-muted">{description}</span>
        </span>
      </button>
    )
  }

  return (
    <section className="mb-5">
      <div className="mb-3 grid gap-3 sm:grid-cols-2">
        {actionCard('upload', ImagePlus, c.upload, c.uploadCardHint)}
        {actionCard('create', Sparkles, c.create, c.createCardHint)}
      </div>
      {draft.mode && (
        <SettingCard divided={false}>
          <form
            className="flex flex-col gap-4 p-4"
            onSubmit={event => {
              event.preventDefault()
              void submit()
            }}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                {upload ? (
                  <ImagePlus aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-accent" />
                ) : (
                  <Sparkles aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-accent" />
                )}
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold text-strong">{upload ? c.upload : c.create}</h3>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{upload ? c.uploadHint : c.createHint}</p>
                </div>
              </div>
              <button
                aria-label={c.close}
                className={cn(BTN_SUBTLE, 'shrink-0 whitespace-nowrap')}
                disabled={busy}
                onClick={() => patch({ mode: null })}
                type="button"
              >
                <X aria-hidden="true" className="size-3.5" />
                <span>{c.close}</span>
              </button>
            </div>

            {!upload && (
              <label>
                <span className={FIELD_LABEL}>{c.kind}</span>
                <PanelSelect
                  ariaLabel={c.kind}
                  disabled={busy}
                  onChange={value => {
                    const assetKind = value

                    if (assetKind === 'actor' || assetKind === 'scene' || assetKind === 'cinematic') {
                      patch({ assetKind })
                    }
                  }}
                  options={[
                    { value: 'cinematic', label: t.assetKindLabels.cinematic },
                    { value: 'scene', label: t.assetKindLabels.scene },
                    { value: 'actor', label: t.assetKindLabels.actor }
                  ]}
                  value={draft.assetKind}
                  widthClass="w-full"
                />
              </label>
            )}

            {upload ? (
              <>
                {!file ? (
                  <>
                    <button
                      aria-describedby="visual-upload-hint"
                      aria-label={c.dropzoneTitle}
                      className={cn(
                        'flex min-h-[190px] w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-8 text-center transition',
                        'focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2',
                        dragging
                          ? 'border-accent bg-accent-soft/35 text-strong'
                          : 'border-line-strong bg-fill-faint text-body hover:border-accent-line hover:bg-fill-hover'
                      )}
                      disabled={busy}
                      onClick={openPicker}
                      onDragEnter={event => {
                        event.preventDefault()
                        setDragging(true)
                      }}
                      onDragLeave={event => {
                        event.preventDefault()
                        setDragging(false)
                      }}
                      onDragOver={event => {
                        event.preventDefault()
                        event.dataTransfer.dropEffect = 'copy'
                        setDragging(true)
                      }}
                      onDrop={handleDrop}
                      type="button"
                    >
                      <span
                        className={cn(
                          'grid size-12 place-items-center rounded-2xl',
                          dragging ? 'bg-accent text-on-accent' : 'bg-accent-soft text-accent'
                        )}
                      >
                        <FolderOpen aria-hidden="true" className="size-6" />
                      </span>
                      <span className="mt-4 text-sm font-semibold text-strong">
                        {dragging ? c.dropzoneActive : c.dropzoneTitle}
                      </span>
                      <span className="mt-1 text-xs text-muted">{c.dropzoneHint}</span>
                      <span className={cn(BTN_PRIMARY, 'mt-4 h-9 px-5')}>{c.browse}</span>
                    </button>
                    <input
                      accept={VISUAL_ACCEPT}
                      className="sr-only"
                      disabled={busy}
                      onChange={event => {
                        const next = event.currentTarget.files?.[0]

                        if (next) {
                          selectFile(next)
                        }

                        event.currentTarget.value = ''
                      }}
                      ref={inputRef}
                      type="file"
                    />
                    <p className="text-center text-[11px] text-faint" id="visual-upload-hint">
                      {c.supportedFormats}
                    </p>
                  </>
                ) : (
                  <div className="grid gap-3 rounded-2xl border border-accent-line bg-accent-soft/15 p-3 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                    <div className="flex min-h-44 items-center justify-center overflow-hidden rounded-xl bg-fill-trough">
                      {preview && isVideoFile(file) ? (
                        <video
                          className="max-h-64 w-full object-contain"
                          controls
                          muted
                          preload="metadata"
                          src={preview}
                        />
                      ) : preview ? (
                        <img alt={file.name} className="max-h-64 w-full object-contain" src={preview} />
                      ) : null}
                    </div>
                    <div className="flex min-w-0 flex-col justify-between gap-4 p-1">
                      <div>
                        <div className="flex items-start gap-2">
                          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
                            {isVideoFile(file) ? (
                              <Video aria-hidden="true" className="size-4" />
                            ) : (
                              <FileImage aria-hidden="true" className="size-4" />
                            )}
                          </span>
                          <div className="min-w-0">
                            <p className="break-all text-sm font-medium text-strong">{file.name}</p>
                            <p className="mt-1 text-[11px] text-muted">
                              {isVideoFile(file) ? c.video : c.image} · {formatFileSize(file.size)}
                            </p>
                          </div>
                          <CheckCircle2 aria-hidden="true" className="ml-auto size-4 shrink-0 text-success" />
                        </div>
                        <p className="mt-3 text-xs leading-relaxed text-muted">{c.fileReady}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button className={BTN_SUBTLE} disabled={busy} onClick={openPicker} type="button">
                          <FolderOpen aria-hidden="true" className="size-3.5" />
                          {c.replaceFile}
                        </button>
                        <button className={BTN_GHOST} disabled={busy} onClick={removeFile} type="button">
                          <X aria-hidden="true" className="size-3.5" />
                          {c.removeFile}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
                {(fileError || error) && (
                  <p className="text-xs text-danger-fg" role="alert">
                    {fileError || error}
                  </p>
                )}
              </>
            ) : (
              <>
                {draft.assetKind !== 'scene' && materialSelect(c.outfit, 'outfitId', materials.outfits)}
                {draft.assetKind !== 'actor' && materialSelect(c.scene, 'sceneId', materials.scenes)}
                {draft.assetKind !== 'scene' && materialSelect(c.action, 'actionId', materials.actions)}
                {draft.assetKind === 'cinematic' && (
                  <label>
                    <span className={FIELD_LABEL}>{t.mediaKindLabel}</span>
                    <PanelSelect
                      ariaLabel={t.mediaKindLabel}
                      disabled={busy}
                      onChange={value => patch({ mediaKind: value === 'video' ? 'video' : 'image' })}
                      options={[
                        { value: 'image', label: c.image },
                        { value: 'video', label: c.video }
                      ]}
                      value={draft.mediaKind}
                      widthClass="w-full"
                    />
                  </label>
                )}
                <label>
                  <span className={FIELD_LABEL}>{c.requirements}</span>
                  <textarea
                    className={cn(INPUT_CLASS, 'min-h-24 resize-y')}
                    disabled={busy}
                    maxLength={2000}
                    onChange={event => patch({ requirements: event.target.value })}
                    placeholder={c.requirementsPlaceholder}
                    value={draft.requirements}
                  />
                </label>
                {!visualCreationReady(draft) && <p className="text-xs text-muted">{c.noMaterials}</p>}
              </>
            )}

            {!upload && error && (
              <p className="text-xs text-danger-fg" role="alert">
                {error}
              </p>
            )}

            <div className="sticky bottom-0 z-10 -mx-4 -mb-4 flex flex-wrap items-center justify-between gap-3 border-t border-line-standard bg-surface-panel/95 px-4 py-3 backdrop-blur">
              <p className="text-[11px] leading-relaxed text-faint">
                {upload
                  ? file
                    ? c.readyToUpload
                    : c.chooseFileFirst
                  : visualCreationReady(draft)
                    ? c.readyToCreate
                    : c.chooseMaterialsFirst}
              </p>
              <button
                className={cn(BTN_PRIMARY, 'h-10 px-5')}
                disabled={busy || (upload ? !file : !visualCreationReady(draft))}
                type="submit"
              >
                {busy ? (upload ? c.uploading : c.creating) : upload ? c.uploadSubmit : c.submit}
              </button>
            </div>
          </form>
        </SettingCard>
      )}
    </section>
  )
}
