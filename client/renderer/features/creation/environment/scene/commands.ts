import { captureAuthScope } from '@/shared/lib/authed-api'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch } from '@/shared/lib/storage'

import { hydrateScene, loadSceneLibrary } from './library'
import {
  $pendingScene,
  $sceneCreationIssue,
  $sceneMutationIssues,
  $sceneRegenerating,
  $sceneTaskSlow,
  $sceneTaskStatus,
  mutate,
  mutateSceneRow,
  type SceneCreationIssue,
  type SceneCreationResult,
  sceneError,
  type SceneGenerationInput,
  sceneIO,
  type SceneMutationIssue,
  type ScenePolicy,
  sceneRequest,
  type SceneWire,
  startPoll,
  toScene
} from './state'

export async function inspectSceneCreationIssue(): Promise<boolean> {
  const previous = $sceneCreationIssue.get()
  const scope = captureAuthScope()

  if (!scope || previous?.outcome !== 'unknown') {
    return false
  }

  const issue = previous.inspected ? { ...previous, inspected: false } : previous
  $sceneCreationIssue.set(issue)

  try {
    const [stateRead, libraryRead] = await Promise.all([
      hydrateScene({ skipLibraryRefresh: true }),
      loadSceneLibrary('', 0)
    ])

    if (!scope() || issue !== $sceneCreationIssue.get() || !stateRead || !libraryRead) {
      return false
    }

    $sceneCreationIssue.set({ ...issue, inspected: true })

    return true
  } catch (error) {
    log.warn('scene', 'Scene creation inspection failed:', error)

    return false
  }
}

/** 只结束用户已核对的本地等待提醒；不推断提交结局，也不取消或重发制作。 */
export function finishSceneCreationWait(issue: SceneCreationIssue): void {
  if (issue === $sceneCreationIssue.get() && issue.outcome === 'unknown' && issue.inspected) {
    $sceneCreationIssue.set(null)
  }
}

/** 丢弃当前创建失败提醒（不含「结果未知」，未知必须先核对原任务）。 */
export function discardSceneCreationIssue(): void {
  if ($sceneCreationIssue.get()?.outcome === 'rejected') {
    $sceneCreationIssue.set(null)
  }
}

export function updateSceneMutationIssue(
  sceneId: string,
  update: (current: SceneMutationIssue | null) => SceneMutationIssue | null
): void {
  const issues = $sceneMutationIssues.get()
  const previous = issues[sceneId] ?? null
  const issue = update(previous)

  if (issue === previous) {
    return
  }

  const next = { ...issues }

  if (issue) {
    next[sceneId] = issue
  } else {
    delete next[sceneId]
  }

  $sceneMutationIssues.set(next)
}

export async function createScene(input: SceneGenerationInput = {}): Promise<SceneCreationResult> {
  if (
    sceneIO.submitting ||
    $sceneTaskStatus.get() !== 'none' ||
    $sceneRegenerating.get() !== null ||
    $sceneCreationIssue.get()?.outcome === 'unknown'
  ) {
    return { kind: 'unavailable' }
  }

  const epoch = currentClearEpoch()
  let requestStarted = false
  sceneIO.submitting = true
  $sceneCreationIssue.set(null)

  try {
    requestStarted = true

    const row = await sceneRequest('companion_scenes.create_scene', {
      body: { ...input, source: 'generate' }
    })

    if (epoch === currentClearEpoch()) {
      $pendingScene.set(toScene(row, row.url || ''))
      $sceneTaskStatus.set('pending')
    }

    $sceneTaskSlow.set(false)
    startPoll()
    await hydrateScene()

    if (epoch !== currentClearEpoch()) {
      return { kind: 'unavailable' }
    }

    return { kind: 'created', sceneId: row.id }
  } catch (error) {
    if (epoch === currentClearEpoch()) {
      const issue: SceneCreationIssue = {
        kind: 'failed',
        message: sceneError(error),
        outcome: !requestStarted || isClientErrorIpc(error) ? 'rejected' : 'unknown',
        inspected: false
      }

      $sceneCreationIssue.set(issue)

      return issue
    }

    return { kind: 'unavailable' }
  } finally {
    if (epoch === currentClearEpoch()) {
      sceneIO.submitting = false
    }
  }
}

export async function prepareScenePrompt(
  input: Pick<SceneGenerationInput, 'notes'>
): Promise<{ id: string; prompt: string }> {
  const row = await mutate('companion_scenes.post_scene_prompt', { body: input })

  return { id: row.id, prompt: row.prompt }
}

export async function adoptSceneImage(image: { base64: string }, sceneId?: string): Promise<string | null> {
  const result = await mutate('companion_scenes.create_scene', {
    body: {
      source: 'analysis',
      scene_id: sceneId ? sceneId : undefined,
      image: image.base64
    }
  })

  return result?.id ?? null
}

export async function cancelSceneTask(sceneId: string): Promise<SceneWire> {
  return mutateSceneRow(() => sceneRequest('companion_scenes.post_scene_discard', { path: { scene_id: sceneId } }))
}

export async function deleteScene(sceneId: string): Promise<void> {
  await mutate('companion_scenes.delete_scene_route', { path: { scene_id: sceneId } })
}

export async function editScene(sceneId: string, title: string, description: string): Promise<SceneWire> {
  return mutateSceneRow(() =>
    sceneRequest('companion_scenes.scene_edit_route', {
      path: { scene_id: sceneId },
      body: { title, description }
    })
  )
}

export async function regenerateScene(sceneId: string): Promise<void> {
  await mutateSceneRow(() => sceneRequest('companion_scenes.post_scene_regenerate', { path: { scene_id: sceneId } }))
}

export async function analyzeScene(sceneId: string): Promise<SceneWire> {
  return mutateSceneRow(() => sceneRequest('companion_scenes.scene_analyze_route', { path: { scene_id: sceneId } }))
}

export async function setScenePolicy(policy: ScenePolicy): Promise<void> {
  await mutate('companion_scenes.patch_scene_policy', { body: { policy } })
}

/** 场景素材变更的事件计数；仅用于中止与刷新竞争中的过期详情读取。 */
