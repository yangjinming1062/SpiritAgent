import { type ReactElement, useMemo } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import { useVisualMaterialGroups } from '../shared/use-visual-material-groups'
import {
  type MaterialOption,
  materialSelection,
  visualAnalysisControls,
  visualMaterialControls,
  visualMaterialCopy
} from '../shared/visual-materials'

import { errorText, type RemoteApi } from './api'
import { MediaReviewButton } from './MediaReviewButton'
import { usePageState } from './page-state'
import { useTask } from './tasks'
import { Media, Notice } from './ui'

const a = visualMaterialCopy.zh

export function VisualAnalysis({
  api,
  visualId,
  job,
  onChanged
}: {
  api: RemoteApi
  visualId: string
  job: ApiSchemas['ImportAnalysisView']
  onChanged: () => Promise<void>
}): ReactElement {
  const { run, busy, error } = useTask()
  const { active, review, applyResult, retry, error: jobError } = visualAnalysisControls(job)
  const [notes, setNotes] = usePageState(`visuals.analysisNotes.${visualId}`, '')
  const blockers = job.result?.blocking_reasons ?? []
  const showBlockers = !blockers.some(block => block.code === 'materials_incomplete')
  const read = useMemo(() => api.invoke.bind(api), [api])
  const groups = useVisualMaterialGroups(read, job)
  const materialError = groups.find(group => group.error)?.error

  const command = (body: ApiSchemas['ImportAnalysisRequest']): void => {
    void run(async () => {
      await api.invoke('companion_visuals.analyze_visual', {
        path: { visual_id: visualId },
        body: { ...body, expected_revision: job.revision }
      })
      await onChanged()
    })
  }
  return (
    <div className="visual-materials">
      {review ? <MediaReviewButton api={api} jobId={job.id} /> : null}
      {applyResult ? (
        <button disabled={busy} onClick={() => command({ command: 'apply_result' })} type="button">
          应用分析结果
        </button>
      ) : null}

      {active && (
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
      {groups.map(group => (
        <section aria-label={a.groups[group.kind]} className="card" key={group.kind}>
          <h3>{a.groups[group.kind]}</h3>
          {group.components.length === 0 ? (
            <p className="muted">{!job.result ? (active ? a.working : a.pending) : a.empty}</p>
          ) : (
            group.components.map(component => (
              <MaterialRow
                api={api}
                component={component}
                key={`${visualId}:${component.observation.key}`}
                onChanged={onChanged}
                options={group.options}
                visualId={visualId}
              />
            ))
          )}
        </section>
      ))}
      {materialError ? <Notice error={errorText(materialError)} /> : null}
      {error && <Notice error={error} />}
      {jobError && (
        <p className="muted" role="status">
          {jobError}
        </p>
      )}
      <div className="button-row">
        {active ? (
          <button disabled={busy} onClick={() => command({ command: 'cancel' })} type="button">
            {a.cancel}
          </button>
        ) : retry ? (
          <button disabled={busy} onClick={() => command({ command: 'retry' })} type="button">
            {a.retry}
          </button>
        ) : null}
      </div>
      {job.result && (
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
  component: ApiSchemas['ImportComponentView']
  options: MaterialOption[]
  visualId: string
  onChanged: () => Promise<void>
}): ReactElement {
  const key = `${visualId}:${component.observation.key}`
  const [chosen, setChosen] = usePageState(`visuals.materialSelection.${key}`, '')
  const [notes, setNotes] = usePageState(`visuals.materialNotes.${key}`, component.notes)
  const { run, busy, error } = useTask()
  const controls = visualMaterialControls(component, options)
  const disabled = busy || controls.busy
  const choose = (command: ApiSchemas['ImportSelection']['command'], materialId?: string): void => {
    void run(async () => {
      try {
        await api.invoke('companion_visuals.analyze_visual', {
          path: { visual_id: visualId },
          body: materialSelection(component, command, notes, materialId)
        })
      } finally {
        await onChanged()
      }
    })
  }
  return (
    <article aria-busy={disabled} className="card-divider">
      <div className="card-title">
        <h4>{controls.name}</h4>
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
      {controls.previewPath && <Media type="image" url={controls.previewPath} />}
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
