import type { ReactElement } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import {
  visualAnalysisControls,
  visualMaterialControls,
  visualMaterialCopy,
  visualMaterialGroups
} from '../shared/visual-materials'
import type { VisualMaterialOptions } from '../shared/visuals'

import type { RemoteApi } from './api'
import { useMaterialOptions } from './material-options'
import { Media, Notice, usePageState, useTask } from './ui'

const a = visualMaterialCopy.zh

export function VisualAnalysis({
  api,
  visual,
  onChanged
}: {
  api: RemoteApi
  visual: ApiSchemas['VisualDetailView']
  onChanged: () => Promise<void>
}): ReactElement {
  const { run, busy, error } = useTask()
  const job = visual.analysis
  const { active, canContinue } = visualAnalysisControls(job)
  const [notes, setNotes] = usePageState(`visuals.analysisNotes.${visual.id}`, '')
  const blockers = job?.result?.blocking_reasons ?? []
  const showBlockers = !blockers.some(block => block.code === 'materials_incomplete')
  const materialRevision = job?.result?.components
    .map(row => `${row.material_id}:${row.status}:${row.operation_state}`)
    .join(',')
  const { materials, error: materialError } = useMaterialOptions(api, true, materialRevision)

  const command = (body: ApiSchemas['ImportAnalysisRequest']): void => {
    void run(async () => {
      await api.invoke('companion_visuals.analyze_visual', {
        path: { visual_id: visual.id },
        body: { ...body, expected_revision: job?.revision }
      })
      await onChanged()
    })
  }
  return (
    <div className="visual-materials">
      {active && job && (
        <p className="muted" role="status">
          {a.stages[job.stage] ?? a.working}
        </p>
      )}
      {showBlockers && blockers.length > 0 && (
        <ul className="muted" role="status">
          {blockers.map(block => (
            <li key={block.code}>{block.reason}</li>
          ))}
        </ul>
      )}
      {visualMaterialGroups(job).map(group => (
        <section aria-label={a.groups[group.kind]} className="card" key={group.kind}>
          <h3>{a.groups[group.kind]}</h3>
          {group.components.length === 0 ? (
            <p className="muted">{!job?.result ? (active ? a.working : a.pending) : a.empty}</p>
          ) : (
            group.components.map(component => (
              <MaterialRow
                api={api}
                component={component}
                key={`${visual.id}:${component.observation.key}`}
                onChanged={onChanged}
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
      {materialError && <Notice error={materialError} />}
      {error && <Notice error={error} />}
      {job?.error && canContinue && !job.result?.components.some(component => component.status === 'failed') && (
        <p className="muted" role="status">
          {job.error}
        </p>
      )}
      <div className="button-row">
        {active ? (
          <button disabled={busy} onClick={() => command({ command: 'cancel' })} type="button">
            {a.cancel}
          </button>
        ) : (
          <>
            {canContinue && (
              <button disabled={busy} onClick={() => command({ command: 'retry' })} type="button">
                {job ? a.retry : a.start}
              </button>
            )}
          </>
        )}
      </div>
      {job?.result && (
        <details>
          <summary>{a.manage}</summary>
          <label>
            {a.notes}
            <input maxLength={2000} onChange={event => setNotes(event.target.value)} value={notes} />
          </label>
          <button disabled={busy || active} onClick={() => command({ command: 'reanalyze', notes })} type="button">
            {a.reanalyze}
          </button>
        </details>
      )}
    </div>
  )
}

function MaterialRow({
  api,
  component,
  options,
  visualId,
  onChanged
}: {
  api: RemoteApi
  component: ApiSchemas['ImportComponent']
  options: VisualMaterialOptions['outfits']
  visualId: string
  onChanged: () => Promise<void>
}): ReactElement {
  const key = `${visualId}:${component.observation.key}`
  const [chosen, setChosen] = usePageState(`visuals.materialSelection.${key}`, '')
  const [notes, setNotes] = usePageState(`visuals.materialNotes.${key}`, component.notes)
  const { run, busy, error } = useTask()
  const controls = visualMaterialControls(component)
  const disabled = busy || controls.busy
  const linked = options.find(option => option.id === component.material_id)
  const linkedName = linked?.name ?? component.observation.name
  const previewPath = controls.linked
    ? linked?.preview || (component.created ? component.preview_path : null)
    : component.preview_path
  const choose = (command: ApiSchemas['ImportSelection']['command'], materialId?: string): void => {
    void run(async () => {
      try {
        await api.invoke('companion_visuals.analyze_visual', {
          path: { visual_id: visualId },
          body: {
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
          }
        })
      } finally {
        await onChanged()
      }
    })
  }
  return (
    <article aria-busy={disabled} className="card-divider">
      <div className="card-title">
        <h4>{controls.linked ? linkedName : component.observation.name}</h4>
        <span className="pill">
          {component.operation_state === 'running'
            ? (a.stages[component.operation_stage] ?? a.states.running)
            : a.components[controls.status]}
        </span>
      </div>
      {controls.pose ? (
        <p className="muted">{a.pose}</p>
      ) : component.creation_method === 'generated' ? (
        <p className="muted">{a.generated}</p>
      ) : null}
      {previewPath && <Media type="image" url={previewPath} />}
      {controls.notice && (
        <p className="muted" role="status">
          {component.failure_summary || (controls.confirmation ? component.reason : a.notices[controls.notice])}
        </p>
      )}
      {controls.needsAction && component.can_extract && (
        <button disabled={disabled} onClick={() => choose(controls.extractCommand)} type="button">
          {a[controls.extractLabel]}
        </button>
      )}
      <details open={controls.confirmation || undefined}>
        <summary>{controls.confirmation ? a.confirm : a.manage}</summary>
        <label>
          {a.existing}
          <select disabled={disabled} onChange={event => setChosen(event.target.value)} value={chosen}>
            <option value="">{a.select}</option>
            {options.map(option => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </label>
        <input
          aria-label={a.notes}
          disabled={disabled}
          maxLength={2000}
          onChange={event => setNotes(event.target.value)}
          placeholder={a.notes}
          value={notes}
        />
        <div className="button-row">
          <button disabled={disabled || !chosen} onClick={() => choose('existing', chosen)} type="button">
            {controls.linked ? a.change : a.apply}
          </button>
          {component.created && component.material_id !== null && (
            <button disabled={disabled} onClick={() => choose('undo')} type="button">
              {a.undo}
            </button>
          )}
          {!controls.linked && (
            <button disabled={disabled} onClick={() => choose('skip')} type="button">
              {a.skip}
            </button>
          )}
        </div>
      </details>
      {error && <Notice error={error} />}
    </article>
  )
}
