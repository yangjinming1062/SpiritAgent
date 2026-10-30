import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import { loadVisualMaterials, type VisualMaterialOptions } from '../shared/visuals'

import type { RemoteApi } from './api'
import { errorText } from './api'
import { Media, Notice, useAlive, usePageState, useTask } from './ui'

const STATE: Record<string, string> = {
  queued: '等待分析',
  running: '分析整理中',
  paused: '分析已暂停',
  succeeded: '分析完成',
  failed: '分析需重试',
  cancelled: '分析已取消',
  unknown: '状态待核对'
}
const COMPONENT: Record<string, string> = {
  recognized: '仅识别',
  ambiguous: '待确认',
  reused: '已关联',
  created: '已入库',
  reviewing: '动作评审中',
  failed: '整理需重试',
  skipped: '未关联',
  undone: '已撤销入库'
}
const CONFIDENCE: Record<string, string> = { high: '高置信度', medium: '中等置信度', low: '低置信度' }
const DECISION: Record<string, string> = { same: '一致', different: '不同', uncertain: '尚不确定' }

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
  const active = job?.state === 'queued' || job?.state === 'running'
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const [materialError, setMaterialError] = useState('')
  const [selected, setSelected] = usePageState<Record<string, string>>('visuals.analysisSelections', {})
  const [notes, setNotes] = usePageState(`visuals.analysisNotes.${visual.id}`, '')
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
  }, [api, active, visual.id, alive])

  const command = (body: ApiSchemas['ImportAnalysisRequest']): void => {
    void run(async () => {
      await api.invoke('companion_visuals.analyze_visual', {
        path: { visual_id: visual.id },
        body: { ...body, notes, expected_revision: job?.revision }
      })
      await onChanged()
    })
  }
  const choose = (
    component: ApiSchemas['ImportComponent'],
    operation: ApiSchemas['ImportSelection']['command'],
    materialId?: string
  ): void =>
    command({
      command: 'apply',
      selections: [{ key: component.observation.key, command: operation, material_id: materialId }]
    })
  const source = visual.snapshot.source
  return (
    <section className="card">
      <div className="card-title">
        <h3>识别与素材整理</h3>
        {job && <span className="pill">{STATE[job.state]}</span>}
      </div>
      <p className="muted">
        {active
          ? '正在分析原始影像并核对素材，离开页面后任务仍会继续。'
          : !job
            ? '原件已保存，可以分析内容并整理素材。'
            : source.presentation_approved
              ? '已通过检查，可在合适情境中使用。'
              : '当前仅收藏，尚未满足自动呈现条件。'}
      </p>
      {job?.error && <Notice>{job.error}</Notice>}
      {!active && (
        <label>
          修正或补充说明
          <textarea disabled={busy} maxLength={2000} onChange={event => setNotes(event.target.value)} value={notes} />
        </label>
      )}
      <div className="button-row">
        {active ? (
          <button disabled={busy} onClick={() => command({ command: 'cancel' })} type="button">
            取消分析
          </button>
        ) : (
          <>
            {(!job || job.state !== 'succeeded') && (
              <button disabled={busy} onClick={() => command({ command: 'retry' })} type="button">
                {job ? '继续分析' : '分析影像'}
              </button>
            )}
            {job?.result && (
              <button disabled={busy} onClick={() => command({ command: 'reanalyze' })} type="button">
                重新分析
              </button>
            )}
          </>
        )}
      </div>
      {job?.result && (
        <>
          <p>伙伴身份核对：{job.result.understanding.identity_reason}</p>
          <p className="muted">
            检查依据：{visual.snapshot.review_reason || job.result.understanding.presentation_reason}
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
              <article className="card" key={observation.key}>
                <div className="card-title">
                  <h4>{observation.name}</h4>
                  <span className="pill">{COMPONENT[component.status]}</span>
                </div>
                <p>{observation.description}</p>
                <p className="muted">{component.reason || observation.extraction_reason}</p>
                {component.preview_path && <Media type="image" url={component.preview_path} />}
                {component.matches.length > 0 && (
                  <details>
                    <summary>近似素材与判断依据</summary>
                    {component.matches.map(match => (
                      <p key={match.material_id}>
                        {match.name} · {DECISION[match.decision]} · {CONFIDENCE[match.confidence]}：{match.evidence}
                      </p>
                    ))}
                  </details>
                )}
                {!active && (
                  <>
                    <label>
                      选择已有素材
                      <select
                        disabled={busy}
                        onChange={event => setSelected(previous => ({ ...previous, [key]: event.target.value }))}
                        value={chosen}
                      >
                        <option value="">请选择</option>
                        {options.map(option => (
                          <option key={option.id} value={option.id}>
                            {option.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="button-row">
                      <button
                        disabled={busy || !chosen}
                        onClick={() => choose(component, 'existing', chosen)}
                        type="button"
                      >
                        关联此素材
                      </button>
                      {observation.extractable && component.material_id === null && (
                        <button disabled={busy} onClick={() => choose(component, 'extract')} type="button">
                          提取并入库
                        </button>
                      )}
                      {component.created && component.material_id !== null && (
                        <button disabled={busy} onClick={() => choose(component, 'undo')} type="button">
                          撤销本次入库
                        </button>
                      )}
                      <button disabled={busy} onClick={() => choose(component, 'skip')} type="button">
                        暂不关联
                      </button>
                    </div>
                  </>
                )}
              </article>
            )
          })}
        </>
      )}
      {materialError && <Notice error={materialError} />}
      {error && <Notice error={error} />}
    </section>
  )
}
