import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { definePersistedEnum } from '@/shared/lib/storage'

import type { CharacterRenderMode } from './types'

export type { CharacterRenderMode }

const renderModePersisted = definePersistedEnum<CharacterRenderMode>({
  allowed: ['model', 'video'] as const,
  fallback: 'video',
  key: 'da.companion.renderMode'
})

export const $renderMode = renderModePersisted.$atom
export const setRenderMode = renderModePersisted.set

/** 切换渲染方式：切到模型时由后端顺带触发模型生成；偏好只决定界面分区与生成意图，
 * 桌面实际渲染按各形象资产的就绪情况决定（视频优先，其次模型，均未就绪走通用兜底）。 */
export async function switchRenderMode(mode: CharacterRenderMode): Promise<void> {
  const previous = $renderMode.get()

  // 幂等守卫：render_mode.changed 广播回流时会带着当前值再调一次，
  // 无守卫会重复 POST 并再次触发后端广播形成回环。
  if (mode === previous) {
    return
  }

  const result = await authedApi<{ render_mode?: string }>({
    body: { render_mode: mode },
    method: 'POST',
    path: '/api/companion/render-mode'
  })

  // setRenderMode 必须放在 authedApi 之后：与 persona-store setRenderMode 同样的登出 race，
  // 在 IPC 之前写 localStorage 会让广播清不掉这次写入，下一位用户读到错的 renderMode。
  if (!result.ok) {
    // 仅在后端真正报错时回滚；auth-loss 是用户主动登出，回滚无意义且会污染 UI。
    if (result.reason === 'err') {
      log.warn('render-mode', 'switchRenderMode failed; rolling back', result.error)
      setRenderMode(previous)
    }

    return
  }

  setRenderMode(mode)
}
