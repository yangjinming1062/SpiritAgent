import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { MediaPreview } from '@/features/media'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { usePanelAction } from '@/shared/hooks/use-panel-action'
import { failureText } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, PanelSelect, Pill } from '@/ui'
import { MediaReviewButton } from '@/ui/media-review-button'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import {
  visualAnalysisControls,
  visualMaterialControls,
  visualMaterialGroups
} from '../../../../../shared/visual-materials'
import { loadVisualMaterials, type VisualMaterialOptions } from '../../../../../shared/visuals'

import { visualsApi } from './api'
import { type VisualDetail } from './types'
import { VISUAL_BODY, VISUAL_FIELD, VISUAL_META } from './visual-text'
import { $visualsUi, updateVisualsUi } from './visuals-state'
import { loadVisualDetail, refreshVisuals } from './visuals-store'

export function VisualImportAnalysis({ visual }: { visual: VisualDetail }): React.JSX.Element {
  const a = useStrings().visuals.analysis
  const job = visual.analysis
  const { active, canContinue } = visualAnalysisControls(job)
  const { run, busy } = usePanelAction()
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const [error, setError] = useState('')
  const begin = useAsyncGuard()
  const ui = useStore($visualsUi)
  const notesKey = `${visual.id}:analysis`
  const notes = ui.analysisNotes?.[notesKey] ?? ''
  const blockers = job?.result?.blocking_reasons ?? []
  const showBlockers = !blockers.some(block => block.code === 'materials_incomplete')

  const materialRevision = job?.result?.components
    .map(row => `${row.material_id}:${row.status}:${row.operation_state}`)
    .join(',')

  useEffect(() => {
    const live = begin()
    let current = true
    void loadVisualMaterials({
      outfits: () => requestResource('companion.get_outfits'),
      scenes: cursor => requestResource('companion_scenes.scene_list_route', { query: { cursor, limit: 100 } }),
      actions: cursor =>
        requestResource('companion_actions.list_actions', { query: { cursor, limit: 100, group: 'in_library' } })
    })
      .then(value => {
        if (current && live()) {
          setMaterials(value)
          setError('')
        }
      })
      .catch(failure => {
        if (current && live()) {
          setError(failureText(failure, a.failed))
        }
      })

    return () => {
      current = false
    }
  }, [visual.id, materialRevision, begin, a.failed])

  const command = (body: ApiSchemas['ImportAnalysisRequest']): void => {
    void run(
      async () => {
        await visualsApi.analyze(visual.id, { ...body, expected_revision: job?.revision })
        await Promise.all([loadVisualDetail(visual.id, { fresh: true }), refreshVisuals()])
      },
      { failure: a.failed }
    )
  }

  return (
    <div className="space-y-4">
      {job?.stage === 'review_pending' ? <MediaReviewButton jobId={job.id} /> : null}
      {job?.stage === 'confirmation' ? (
        <button disabled={busy} onClick={() => command({ command: 'apply_result' })} type="button">
          {a.applyResult}
        </button>
      ) : null}

      {active && job ? (
        <p className={VISUAL_BODY} role="status">
          {a.stages[job.stage] ?? a.working}
        </p>
      ) : null}
      {showBlockers && blockers.length > 0 ? (
        <ul className={`list-inside list-disc space-y-1 ${VISUAL_BODY}`} role="status">
          {blockers.map(block => (
            <li key={block.code}>{block.reason}</li>
          ))}
        </ul>
      ) : null}
      {visualMaterialGroups(job).map(group => (
        <section
          aria-label={a.groups[group.kind]}
          className="space-y-3 rounded-xl border border-line-hairline bg-fill-faint/40 p-4"
          key={group.kind}
        >
          <h3 className={VISUAL_FIELD}>{a.groups[group.kind]}</h3>
          {group.components.length === 0 ? (
            <p className={VISUAL_META}>{!job?.result ? (active ? a.working : a.pending) : a.empty}</p>
          ) : (
            group.components.map(component => (
              <MaterialRow
                component={component}
                key={`${visual.id}:${component.observation.key}`}
                options={
                  group.kind === 'outfit'
                    ? materials.outfits
                    : group.kind === 'scene'
                      ? materials.scenes
                      : materials.actions
                }
                visualId={visual.id}
              />
            ))
          )}
        </section>
      ))}
      {error ? (
        <p className="text-sm text-danger-fg" role="alert">
          {error}
        </p>
      ) : null}
      {job?.error && canContinue && !job.result?.components.some(component => component.status === 'failed') ? (
        <p className={VISUAL_BODY} role="status">
          {job.error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {active ? (
          <button className={BTN_SUBTLE} disabled={busy} onClick={() => command({ command: 'cancel' })} type="button">
            {a.cancel}
          </button>
        ) : (
          <>
            {canContinue && job?.stage !== 'review_pending' && job?.stage !== 'confirmation' ? (
              <button
                className={BTN_SUBTLE}
                disabled={busy}
                onClick={() => command({ command: 'retry' })}
                type="button"
              >
                {job ? a.retry : a.start}
              </button>
            ) : null}
          </>
        )}
      </div>
      {job?.result ? (
        <details className="space-y-3">
          <summary className={`cursor-pointer ${VISUAL_META}`}>{a.manage}</summary>
          <label className={`block space-y-2 ${VISUAL_BODY}`}>
            <span>{a.notes}</span>
            <input
              className={INPUT_CLASS}
              maxLength={2000}
              onChange={event =>
                updateVisualsUi({
                  analysisNotes: { ...$visualsUi.get().analysisNotes, [notesKey]: event.currentTarget.value }
                })
              }
              value={notes}
            />
          </label>
          <button
            className={BTN_GHOST}
            disabled={busy || active}
            onClick={() => command({ command: 'reanalyze', notes })}
            type="button"
          >
            {a.reanalyze}
          </button>
        </details>
      ) : null}
    </div>
  )
}

function MaterialRow({
  component,
  options,
  visualId
}: {
  component: ApiSchemas['ImportComponentView']
  options: VisualMaterialOptions['outfits']
  visualId: string
}): React.JSX.Element {
  const a = useStrings().visuals.analysis
  const ui = useStore($visualsUi)
  const key = `${visualId}:${component.observation.key}`
  const chosen = ui.analysisSelections?.[key] ?? ''
  const notes = ui.analysisNotes?.[key] ?? component.notes
  const { run, busy } = usePanelAction()
  const controls = visualMaterialControls(component)
  const disabled = busy || controls.busy
  const linked = options.find(option => option.id === component.material_id)
  const linkedName = linked?.name ?? component.observation.name

  const previewPath = controls.linked
    ? linked?.preview || (component.created ? component.preview_path : null)
    : component.preview_path

  const choose = (command: ApiSchemas['ImportSelection']['command'], materialId?: string): void => {
    void run(
      async () => {
        try {
          await visualsApi.analyze(visualId, {
            command: 'apply',
            selections: [
              {
                key: component.observation.key,
                command,
                material_id: materialId,
                expected_component_revision: component.revision,
                notes
              }
            ]
          })
        } finally {
          await Promise.all([loadVisualDetail(visualId, { fresh: true }), refreshVisuals()])
        }
      },
      { failure: a.failed }
    )
  }

  return (
    <article aria-busy={disabled} className="space-y-3 border-t border-line-hairline pt-3">
      <div className="flex items-center gap-2">
        <h4 className={`min-w-0 flex-1 ${VISUAL_FIELD}`}>
          {controls.linked ? linkedName : component.observation.name}
        </h4>
        <Pill tone={component.operation_state === 'failed' ? 'warning' : controls.linked ? 'accent' : 'muted'}>
          {component.operation_state === 'running'
            ? (a.stages[component.operation_stage] ?? a.states.running)
            : a.components[controls.status]}
        </Pill>
      </div>
      {controls.pose ? (
        <p className={VISUAL_META}>{a.pose}</p>
      ) : component.creation_method === 'generated' ? (
        <p className={VISUAL_META}>{a.generated}</p>
      ) : null}
      {previewPath ? (
        <MediaPreview type="image" url={previewPath}>
          {({ src, status, onError }) =>
            status === 'ready' ? (
              <img
                alt={component.observation.name}
                className="max-h-40 rounded-lg object-contain"
                onError={onError}
                src={src}
              />
            ) : null
          }
        </MediaPreview>
      ) : null}
      {controls.notice ? (
        <p className={VISUAL_BODY} role="status">
          {component.failure_summary || (controls.confirmation ? component.reason : a.notices[controls.notice])}
        </p>
      ) : null}
      {controls.needsAction && component.can_extract ? (
        <button
          className={BTN_PRIMARY}
          disabled={disabled}
          onClick={() => choose(controls.extractCommand)}
          type="button"
        >
          {a[controls.extractLabel]}
        </button>
      ) : null}
      <details className="space-y-3" open={controls.confirmation || undefined}>
        <summary className={`cursor-pointer ${VISUAL_META}`}>{controls.confirmation ? a.confirm : a.manage}</summary>
        <PanelSelect
          ariaLabel={a.existing}
          disabled={disabled}
          onChange={value =>
            updateVisualsUi({ analysisSelections: { ...$visualsUi.get().analysisSelections, [key]: value } })
          }
          options={[
            { value: '', label: a.select },
            ...options.map(option => ({ value: option.id, label: option.name }))
          ]}
          value={chosen}
          widthClass="w-full"
        />
        <input
          aria-label={a.notes}
          className={INPUT_CLASS}
          disabled={disabled}
          maxLength={2000}
          onChange={event =>
            updateVisualsUi({ analysisNotes: { ...$visualsUi.get().analysisNotes, [key]: event.currentTarget.value } })
          }
          placeholder={a.notes}
          value={notes}
        />
        <div className="flex flex-wrap gap-2">
          <button
            className={chosen ? BTN_PRIMARY : BTN_SUBTLE}
            disabled={disabled || !chosen}
            onClick={() => choose('existing', chosen)}
            type="button"
          >
            {controls.linked ? a.change : a.apply}
          </button>
          {component.created && component.material_id !== null ? (
            <button className={BTN_GHOST} disabled={disabled} onClick={() => choose('undo')} type="button">
              {a.undo}
            </button>
          ) : null}
          {!controls.linked ? (
            <button className={BTN_GHOST} disabled={disabled} onClick={() => choose('skip')} type="button">
              {a.skip}
            </button>
          ) : null}
        </div>
      </details>
    </article>
  )
}
