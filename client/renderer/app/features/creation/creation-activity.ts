import { computed } from 'nanostores'

import { $pendingScene, $sceneRegenerating } from '@/modules/scene'
import { $visualsJobs } from '@/modules/visuals'
import type { CreationSection } from '@ipc/contracts'

export interface CreationActivityItem {
  id: string
  kind: 'visual' | 'environment'
  state: string
  section: CreationSection
}

/**
 * 创作任务的统一投影：只合并各业务模块已有的状态，不新增状态源与轮询。
 * 状态归属仍在 visuals 与 scene 模块，这里只负责"有没有在进行的事"。
 */
export const $creationActivity = computed(
  [$visualsJobs, $pendingScene, $sceneRegenerating],
  (jobs, pendingScene, regeneratingScene) => {
    const items: CreationActivityItem[] = []

    for (const job of jobs) {
      if (job.state === 'succeeded' || job.state === 'cancelled') {
        continue
      }

      items.push({ id: `visual:${job.id}`, kind: 'visual', state: job.state, section: 'visuals' })
    }

    if (pendingScene) {
      items.push({
        id: `environment:${pendingScene.id}`,
        kind: 'environment',
        state: pendingScene.stage || 'pending',
        section: 'environment'
      })
    } else if (regeneratingScene) {
      items.push({
        id: `environment:${regeneratingScene.id}`,
        kind: 'environment',
        state: 'pending',
        section: 'environment'
      })
    }

    return items
  }
)

export const $creationBusy = computed($creationActivity, items => items.length > 0)
