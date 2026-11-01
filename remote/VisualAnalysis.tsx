import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import {
  visualAnalysisControls,
  visualMaterialControls,
  visualMaterialCopy,
  visualMaterialGroups
} from '../shared/visual-materials'
import { loadVisualMaterials, type VisualMaterialOptions } from '../shared/visuals'

import type { RemoteApi } from './api'
import { errorText } from './api'
import { Media, Notice, useAlive, usePageState, useTask } from './ui'

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
  const alive = useAlive()
  const { run, busy, error } = useTask()
  const job = visual.analysis
  const { active, canContinue } = visualAnalysisControls(job)
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const [materialError, setMaterialError] = useState('')
  const materialRevision = job?.result?.components
    .map(row => `${row.material_id}:${row.status}:${row.operation_state}`)
    .join(',')
  useEffect(() => {
    let current = true
    void loadVisualMaterials({
      outfits: () => api.invoke('companion.get_outfits'),
      scenes: cursor => api.invoke('companion_scenes.scene_list_route', { query: { cursor, limit: 100 } }),
      actions: cursor =>
        api.invoke('companion_actions.list_actions', { query: { cursor, limit: 100, group: 'in_library' } })
    })
      .then(value => {
        if (current && alive()) {
          setMaterials(value)
          setMaterialError('')
        }
      })
      .catch(failure => {
        if (current && alive()) {
          setMaterialError(errorText(failure))
        }
      })
    return () => {
      current = false
    }
  }, [api, materialRevision, visual.id, alive])

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
      {job?.error && canContinue && (
        <p className="muted" role="status">
          {job.stage === 'presentation_review' ? a.collection : a.states[job.state]}
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
            {job?.result && (
              <button disabled={busy} onClick={() => command({ command: 'reanalyze' })} type="button">
                {a.reanalyze}
              </button>
            )}
          </>
        )}
      </div>
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
  const [notes, setNotes] = usePageState(`visuals.materialNotes.${key}`, '')
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
          {a.notices[controls.notice]}
        </p>
      )}
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
        {component.can_extract && (
          <button disabled={disabled} onClick={() => choose(controls.extractCommand)} type="button">
            {a[controls.extractLabel]}
          </button>
        )}
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
      {error && <Notice error={error} />}
    </article>
  )
}
