import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { MediaPreview } from '@/modules/media'
import { loadVisualDetail, refreshVisuals, type VisualDetail, visualsApi } from '@/modules/visuals'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { failureText } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { Badge, BTN_SUBTLE, FIELD_LABEL, INPUT_CLASS, SettingCard } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { loadVisualMaterials, type VisualMaterialOptions } from '../../../../../shared/visuals'

import { $visualsUi, updateVisualsUi } from './visuals-state'

export function VisualImportAnalysis({ visual }: { visual: VisualDetail }): React.JSX.Element {
  const t = useStrings().visuals
  const a = t.analysis
  const job = visual.analysis
  const active = job?.state === 'queued' || job?.state === 'running'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ui = useStore($visualsUi)
  const selected = ui.analysisSelections ?? {}
  const notes = ui.analysisNotes?.[visual.id] ?? ''
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const begin = useAsyncGuard()

  useEffect(() => {
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
          setError(failureText(failure, a.failed))
        }
      })
  }, [visual.id, active, begin, a.failed])

  useEffect(() => {
    if (!active) {
      return
    }

    const live = begin()

    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && live()) {
        void loadVisualDetail(visual.id).catch(failure => {
          if (live()) {
            setError(failureText(failure, a.failed))
          }
        })
      }
    }, 2500)

    return () => window.clearInterval(timer)
  }, [active, visual.id, begin, a.failed])

  const command = async (body: ApiSchemas['ImportAnalysisRequest']): Promise<void> => {
    if (busy) {
      return
    }

    const live = begin()
    setBusy(true)
    setError('')

    try {
      await visualsApi.analyze(visual.id, { ...body, notes, expected_revision: job?.revision })
      await Promise.all([loadVisualDetail(visual.id), refreshVisuals()])
    } catch (failure) {
      if (live()) {
        setError(failureText(failure, a.failed))
      }
    } finally {
      if (live()) {
        setBusy(false)
      }
    }
  }

  const select = (
    component: ApiSchemas['ImportComponent'],
    operation: ApiSchemas['ImportSelection']['command'],
    materialId?: string
  ): void => {
    void command({
      command: 'apply',
      selections: [{ key: component.observation.key, command: operation, material_id: materialId }]
    })
  }

  const source = visual.snapshot.source

  return (
    <SettingCard divided={false}>
      <section className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium text-strong">{a.title}</h3>
          {job && <Badge>{a.states[job.state]}</Badge>}
          {job && <span className="text-xs text-muted">{a.stages[job.stage] ?? job.stage}</span>}
        </div>
        <p className="text-xs text-muted">
          {active ? a.working : !job ? a.idle : source.presentation_approved ? a.approved : a.collection}
        </p>
        {job?.error && (
          <p className="text-xs text-red-500" role="status">
            {job.error}
          </p>
        )}
        {!active && (
          <label>
            <span className={FIELD_LABEL}>{a.notes}</span>
            <textarea
              className={INPUT_CLASS}
              disabled={busy}
              maxLength={2000}
              onChange={event =>
                updateVisualsUi({ analysisNotes: { ...ui.analysisNotes, [visual.id]: event.target.value } })
              }
              value={notes}
            />
          </label>
        )}
        <div className="flex flex-wrap gap-2">
          {active ? (
            <button
              className={BTN_SUBTLE}
              disabled={busy}
              onClick={() => void command({ command: 'cancel' })}
              type="button"
            >
              {a.cancel}
            </button>
          ) : (
            <>
              {(!job || job.state !== 'succeeded') && (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={() => void command({ command: 'retry' })}
                  type="button"
                >
                  {job ? a.retry : a.start}
                </button>
              )}
              {job?.result && (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={() => void command({ command: 'reanalyze' })}
                  type="button"
                >
                  {a.reanalyze}
                </button>
              )}
            </>
          )}
        </div>
        {job?.result && (
          <>
            <p className="text-xs text-muted">
              {a.identity}：{job.result.understanding.identity_reason}
            </p>
            <p className="text-xs text-muted">
              {a.review}：{visual.snapshot.review_reason || job.result.understanding.presentation_reason}
            </p>
            {job.result.components.map(component => {
              const observation = component.observation
              const key = `${visual.id}:${observation.key}`

              const options =
                observation.kind === 'outfit'
                  ? materials.outfits
                  : observation.kind === 'scene'
                    ? materials.scenes
                    : materials.actions

              const chosen = selected[key] ?? ''

              return (
                <article className="rounded-lg border border-edge p-3" key={observation.key}>
                  <div className="mb-2 flex items-center gap-2">
                    <h4 className="text-xs font-medium text-strong">{observation.name}</h4>
                    <Badge>{a.components[component.status]}</Badge>
                  </div>
                  <p className="whitespace-pre-wrap text-xs text-muted">{observation.description}</p>
                  <p className="mt-1 text-xs text-muted">{component.reason || observation.extraction_reason}</p>
                  {component.preview_path && (
                    <MediaPreview type="image" url={component.preview_path}>
                      {({ src, status, onError }) =>
                        status === 'ready' ? (
                          <img
                            alt={observation.name}
                            className="my-2 max-h-48 object-contain"
                            onError={onError}
                            src={src}
                          />
                        ) : null
                      }
                    </MediaPreview>
                  )}
                  {component.matches.length > 0 && (
                    <details className="my-2 text-xs text-muted">
                      <summary>{a.candidate}</summary>
                      {component.matches.map(match => (
                        <p className="mt-1" key={match.material_id}>
                          {match.name} · {a.decisions[match.decision]} · {a.confidence[match.confidence]}：
                          {match.evidence}
                        </p>
                      ))}
                    </details>
                  )}
                  {!active && (
                    <div className="mt-2 flex flex-col gap-2">
                      <label>
                        <span className={FIELD_LABEL}>{a.existing}</span>
                        <select
                          className={INPUT_CLASS}
                          disabled={busy}
                          onChange={event =>
                            updateVisualsUi({ analysisSelections: { ...selected, [key]: event.target.value } })
                          }
                          value={chosen}
                        >
                          <option value="">{a.select}</option>
                          {options.map(option => (
                            <option key={option.id} value={option.id}>
                              {option.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <button
                          className={BTN_SUBTLE}
                          disabled={busy || !chosen}
                          onClick={() => select(component, 'existing', chosen)}
                          type="button"
                        >
                          {a.apply}
                        </button>
                        {observation.extractable && component.material_id === null && (
                          <button
                            className={BTN_SUBTLE}
                            disabled={busy}
                            onClick={() => select(component, 'extract')}
                            type="button"
                          >
                            {a.extract}
                          </button>
                        )}
                        {component.created && component.material_id !== null && (
                          <button
                            className={BTN_SUBTLE}
                            disabled={busy}
                            onClick={() => select(component, 'undo')}
                            type="button"
                          >
                            {a.undo}
                          </button>
                        )}
                        <button
                          className={BTN_SUBTLE}
                          disabled={busy}
                          onClick={() => select(component, 'skip')}
                          type="button"
                        >
                          {a.skip}
                        </button>
                      </div>
                    </div>
                  )}
                </article>
              )
            })}
          </>
        )}
        {error && (
          <p className="text-xs text-red-500" role="alert">
            {error}
          </p>
        )}
      </section>
    </SettingCard>
  )
}
