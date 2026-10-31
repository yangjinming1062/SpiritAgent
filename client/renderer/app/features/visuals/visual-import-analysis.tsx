import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { MediaPreview } from '@/modules/media'
import { loadVisualDetail, refreshVisuals, type VisualDetail, visualsApi } from '@/modules/visuals'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { failureText } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  Disclosure,
  ExpandableText,
  FIELD_LABEL,
  INPUT_CLASS,
  PanelSelect,
  Pill
} from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { loadVisualMaterials, type VisualMaterialOptions } from '../../../../../shared/visuals'

import { isAdvancingState } from './visual-labels'
import { VISUAL_BODY, VISUAL_FIELD, VISUAL_META } from './visual-text'
import { $visualsUi, updateVisualsUi } from './visuals-state'

const COMPONENT_TONE: Record<string, 'accent' | 'muted' | 'warning'> = {
  reused: 'accent',
  created: 'accent',
  ambiguous: 'warning',
  reviewing: 'warning',
  failed: 'warning'
}

export function VisualImportAnalysis({ visual }: { visual: VisualDetail }): React.JSX.Element {
  const t = useStrings().visuals
  const a = t.analysis
  const job = visual.analysis
  const active = isAdvancingState(job?.state)
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

  const stage = job?.stage && job.stage !== 'complete' ? (a.stages[job.stage] ?? job.stage) : ''
  const summary = job ? [a.states[job.state] ?? job.state, stage].filter(Boolean).join(' · ') : a.idle
  const reviewText = visual.snapshot.review_reason || job?.result?.understanding.presentation_reason || ''

  return (
    <Disclosure
      defaultOpen={active || job?.state === 'failed' || !job?.result}
      hint={summary}
      title={a.title}
      variant="panel"
    >
      <div className="space-y-4">
        <p className={VISUAL_BODY}>
          {active ? a.working : !job ? a.idle : source.presentation_approved ? a.approved : a.collection}
        </p>
        {job?.error ? (
          <p className="text-[11px] leading-relaxed text-danger-fg" role="status">
            {job.error}
          </p>
        ) : null}

        <div className="space-y-2 rounded-xl border border-line-hairline bg-fill-faint/40 p-3">
          {!active && (
            <label className="block">
              <span className={FIELD_LABEL}>{a.notes}</span>
              <textarea
                className={INPUT_CLASS}
                disabled={busy}
                maxLength={2000}
                onChange={event =>
                  updateVisualsUi({ analysisNotes: { ...ui.analysisNotes, [visual.id]: event.currentTarget.value } })
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
                {job?.result ? (
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy}
                    onClick={() => void command({ command: 'reanalyze' })}
                    type="button"
                  >
                    {a.reanalyze}
                  </button>
                ) : null}
              </>
            )}
          </div>
        </div>

        {job?.result ? (
          <div className="space-y-3 border-t border-line-hairline pt-4">
            {job.result.understanding.identity_reason ? (
              <LabeledLongText label={a.identity} text={job.result.understanding.identity_reason} />
            ) : null}
            {reviewText ? <LabeledLongText label={a.review} text={reviewText} /> : null}
            {job.result.components.map(component => {
              const observation = component.observation
              const key = `${visual.id}:${observation.key}`
              const evidence = component.reason || observation.extraction_reason
              const extraction = observation.extraction_reason

              const options =
                observation.kind === 'outfit'
                  ? materials.outfits
                  : observation.kind === 'scene'
                    ? materials.scenes
                    : materials.actions

              const chosen = selected[key] ?? ''

              return (
                <article className="space-y-2 rounded-xl border border-line-hairline bg-fill-faint/50 p-3" key={key}>
                  <div className="flex items-center gap-2">
                    <h4 className={`min-w-0 flex-1 truncate ${VISUAL_FIELD}`}>{observation.name}</h4>
                    <Pill tone={COMPONENT_TONE[component.status] ?? 'muted'}>{a.components[component.status]}</Pill>
                  </div>
                  {component.preview_path ? (
                    <MediaPreview type="image" url={component.preview_path}>
                      {({ src, status, onError }) =>
                        status === 'ready' ? (
                          <img
                            alt={observation.name}
                            className="max-h-40 rounded-lg object-contain"
                            onError={onError}
                            src={src}
                          />
                        ) : null
                      }
                    </MediaPreview>
                  ) : null}
                  {evidence ? (
                    <ExpandableText
                      action="text"
                      className={VISUAL_BODY}
                      collapseLabel={t.collapse}
                      expandLabel={t.expand}
                      lines={2}
                      text={evidence}
                    />
                  ) : null}
                  <Disclosure chevron title={a.evidenceTitle} variant="inline">
                    <div className="space-y-2 pt-1">
                      {observation.description ? (
                        <div>
                          <p className={VISUAL_FIELD}>{a.descriptionLabel}</p>
                          <ExpandableText
                            action="text"
                            className={`mt-1 ${VISUAL_BODY}`}
                            collapseLabel={t.collapse}
                            expandLabel={t.expand}
                            lines={6}
                            text={observation.description}
                          />
                        </div>
                      ) : null}
                      {extraction && extraction !== evidence ? (
                        <div>
                          <p className={VISUAL_FIELD}>{a.extractionLabel}</p>
                          <p className={`mt-1 whitespace-pre-wrap ${VISUAL_BODY}`}>{extraction}</p>
                        </div>
                      ) : null}
                      {component.matches.length > 0 ? (
                        <div>
                          <p className={VISUAL_FIELD}>
                            {a.candidate} · {component.matches.length}
                          </p>
                          <ul className="mt-1 space-y-1.5">
                            {component.matches.map(match => (
                              <li className="rounded-lg bg-fill-faint/60 p-2" key={match.material_id}>
                                <p className="text-[12px] text-strong">
                                  {match.name}
                                  <span className={`ml-1.5 ${VISUAL_META}`}>
                                    {a.decisions[match.decision]} · {a.confidence[match.confidence]}
                                  </span>
                                </p>
                                <p className={`mt-0.5 whitespace-pre-wrap ${VISUAL_BODY}`}>{match.evidence}</p>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  </Disclosure>
                  {!active ? (
                    <div className="space-y-2 border-t border-line-hairline pt-3">
                      <label className="block">
                        <span className={FIELD_LABEL}>{a.existing}</span>
                        <PanelSelect
                          ariaLabel={a.existing}
                          disabled={busy}
                          onChange={value => updateVisualsUi({ analysisSelections: { ...selected, [key]: value } })}
                          options={[
                            { value: '', label: a.select },
                            ...options.map(option => ({ value: option.id, label: option.name }))
                          ]}
                          value={chosen}
                          widthClass="w-full"
                        />
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <button
                          className={chosen ? BTN_PRIMARY : BTN_SUBTLE}
                          disabled={busy || !chosen}
                          onClick={() => select(component, 'existing', chosen)}
                          type="button"
                        >
                          {a.apply}
                        </button>
                        {observation.extractable && component.material_id === null ? (
                          <button
                            className={chosen ? BTN_SUBTLE : BTN_PRIMARY}
                            disabled={busy}
                            onClick={() => select(component, 'extract')}
                            type="button"
                          >
                            {a.extract}
                          </button>
                        ) : null}
                        {component.created && component.material_id !== null ? (
                          <button
                            className={BTN_GHOST}
                            disabled={busy}
                            onClick={() => select(component, 'undo')}
                            type="button"
                          >
                            {a.undo}
                          </button>
                        ) : null}
                        <button
                          className={BTN_GHOST}
                          disabled={busy}
                          onClick={() => select(component, 'skip')}
                          type="button"
                        >
                          {a.skip}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </article>
              )
            })}
          </div>
        ) : null}

        {error ? (
          <p className="text-[11px] leading-relaxed text-danger-fg" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Disclosure>
  )
}

/** 分析结论的标签加短预览：标签做锚点，正文默认两行。 */
function LabeledLongText({ label, text }: { label: string; text: string }): React.JSX.Element {
  const t = useStrings().visuals

  return (
    <div>
      <p className={VISUAL_FIELD}>{label}</p>
      <ExpandableText
        action="text"
        className={`mt-1 ${VISUAL_BODY}`}
        collapseLabel={t.collapse}
        expandLabel={t.expand}
        lines={2}
        text={text}
      />
    </div>
  )
}
