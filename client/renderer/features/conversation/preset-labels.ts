import type { Dictionary } from '@/shared/strings'
import type { SessionInfo, SystemPresetSummary } from '@protocol'

// 系统预设的界面名称与说明：后端目录只有中文，已知预设按当前界面语言取字典，未知预设回落目录值。组件传入 useStrings() 的字典以随语言切换重渲染，store 传入 getStrings()。

function localizedName(dict: Dictionary, presetId: string): null | string {
  const names: Record<string, string> = dict.presets.names

  return Object.hasOwn(names, presetId) ? names[presetId] : null
}

export function presetDisplayName(dict: Dictionary, preset: Pick<SystemPresetSummary, 'id' | 'name'>): string {
  return localizedName(dict, preset.id) ?? preset.name
}

export function presetDisplayDescription(
  dict: Dictionary,
  preset: Pick<SystemPresetSummary, 'description' | 'id'>
): string {
  const descriptions: Record<string, string> = dict.presets.descriptions

  return Object.hasOwn(descriptions, preset.id) ? descriptions[preset.id] : (preset.description ?? '')
}

/** 系统对话的角色名由服务端投影；资料尚未就绪时使用本地化占位。 */
export function sessionDisplayTitle(dict: Dictionary, session: Pick<SessionInfo, 'kind' | 'title'>): null | string {
  const title = session.title?.trim() || null

  return title ?? (session.kind === 'special' ? dict.conversation.sessionSidebar.systemSessionFallback : null)
}
