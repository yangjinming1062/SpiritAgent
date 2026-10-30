import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { beginConversationInputActivity } from '@/modules/conversation'
import { visualsApi } from '@/modules/visuals'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { failureText } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { BTN_PRIMARY, BTN_SUBTLE, FIELD_LABEL, INPUT_CLASS, SettingCard } from '@/shared/panel'
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

export function VisualCreatePanel({ onDone }: { onDone: (visualId?: string) => Promise<void> }): React.JSX.Element {
  const t = useStrings().visuals
  const c = t.composer
  const ui = useStore($visualsUi)
  const draft = ui.creation ?? emptyVisualCreation
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
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
      return
    }

    const url = URL.createObjectURL(file)
    setPreview(url)

    return () => {
      URL.revokeObjectURL(url)
      setPreview('')
    }
  }, [file])

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
          filename: file.name,
          title: draft.title,
          description: draft.description
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
        setFile(null)
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
      <select
        className={INPUT_CLASS}
        disabled={busy}
        onChange={event => patch({ [key]: event.target.value })}
        value={draft[key]}
      >
        <option value="">{c.select}</option>
        {options.map(option => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <section className="mb-3">
      <div className="mb-3 flex gap-2">
        <button className={BTN_PRIMARY} disabled={busy} onClick={() => patch({ mode: 'create' })} type="button">
          {c.create}
        </button>
        <button className={BTN_SUBTLE} disabled={busy} onClick={() => patch({ mode: 'upload' })} type="button">
          {c.upload}
        </button>
      </div>
      {draft.mode && (
        <SettingCard divided={false}>
          <form
            className="flex flex-col gap-3 p-4"
            onSubmit={event => {
              event.preventDefault()
              void submit()
            }}
          >
            <div className="flex items-center justify-between">
              <h3>{upload ? c.upload : c.create}</h3>
              <button className={BTN_SUBTLE} disabled={busy} onClick={() => patch({ mode: null })} type="button">
                {c.close}
              </button>
            </div>
            <p className="text-xs text-muted">{upload ? c.uploadHint : c.createHint}</p>
            {!upload && (
              <label>
                <span className={FIELD_LABEL}>{c.kind}</span>
                <select
                  className={INPUT_CLASS}
                  disabled={busy}
                  onChange={event => {
                    const assetKind = event.target.value

                    if (assetKind === 'actor' || assetKind === 'scene' || assetKind === 'cinematic') {
                      patch({ assetKind })
                    }
                  }}
                  value={draft.assetKind}
                >
                  <option value="cinematic">{t.assetKindLabels.cinematic}</option>
                  <option value="scene">{t.assetKindLabels.scene}</option>
                  <option value="actor">{t.assetKindLabels.actor}</option>
                </select>
              </label>
            )}
            {upload ? (
              <>
                <label>
                  <span className={FIELD_LABEL}>{c.file}</span>
                  <input
                    accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov"
                    disabled={busy}
                    onChange={event => setFile(event.target.files?.[0] ?? null)}
                    type="file"
                  />
                </label>
                {!file && <p className="text-xs text-muted">{c.chooseAgain}</p>}
                {file &&
                  preview &&
                  (file.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(file.name) ? (
                    <video className="max-h-64 w-full" controls src={preview} />
                  ) : (
                    <img alt={file.name} className="max-h-64 object-contain" src={preview} />
                  ))}
                <label>
                  <span className={FIELD_LABEL}>{c.title}</span>
                  <input
                    className={INPUT_CLASS}
                    disabled={busy}
                    maxLength={160}
                    onChange={event => patch({ title: event.target.value })}
                    value={draft.title}
                  />
                </label>
                <label>
                  <span className={FIELD_LABEL}>{c.description}</span>
                  <textarea
                    className={INPUT_CLASS}
                    disabled={busy}
                    maxLength={1500}
                    onChange={event => patch({ description: event.target.value })}
                    value={draft.description}
                  />
                </label>
              </>
            ) : (
              <>
                {draft.assetKind !== 'scene' && materialSelect(c.outfit, 'outfitId', materials.outfits)}
                {draft.assetKind !== 'actor' && materialSelect(c.scene, 'sceneId', materials.scenes)}
                {draft.assetKind !== 'scene' && materialSelect(c.action, 'actionId', materials.actions)}
                {draft.assetKind === 'cinematic' && (
                  <label>
                    <span className={FIELD_LABEL}>{t.mediaKindLabel}</span>
                    <select
                      className={INPUT_CLASS}
                      disabled={busy}
                      onChange={event => patch({ mediaKind: event.target.value === 'video' ? 'video' : 'image' })}
                      value={draft.mediaKind}
                    >
                      <option value="image">{c.image}</option>
                      <option value="video">{c.video}</option>
                    </select>
                  </label>
                )}
                <label>
                  <span className={FIELD_LABEL}>{c.requirements}</span>
                  <textarea
                    className={INPUT_CLASS}
                    disabled={busy}
                    maxLength={2000}
                    onChange={event => patch({ requirements: event.target.value })}
                    value={draft.requirements}
                  />
                </label>
                {!visualCreationReady(draft) && <p className="text-xs text-muted">{c.noMaterials}</p>}
              </>
            )}
            {error && (
              <p className="text-xs text-red-500" role="alert">
                {error}
              </p>
            )}
            <div>
              <button
                className={BTN_PRIMARY}
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
