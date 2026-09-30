import type { Dictionary } from '@/shared/strings'
import type { SessionInfo, SystemPresetSummary } from '@/shared/types/spiritagent'

// 系统预设的界面名称与说明：后端目录只有中文，已知预设按当前界面语言取字典，未知预设回落目录值。
// 组件传入 useStrings() 的字典以随语言切换重渲染，store 传入 getStrings()。

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

  return Object.hasOwn(descriptions, preset.id) ? descriptions[preset.id] : preset.description
}

/**
 * 会话的显示标题：固定预设会话（kind=special）显示预设名——库内标题是创建时的中文目录名，不随界面语言变化；
 * 其余会话用自身标题。都取不到时返回 null，由调用方选择占位文案。
 */
export function sessionDisplayTitle(
  dict: Dictionary,
  session: Pick<SessionInfo, 'kind' | 'system_preset_id' | 'title'>,
  presets: readonly SystemPresetSummary[] = []
): null | string {
  const title = session.title?.trim() || null
  const presetId = session.system_preset_id

  if (session.kind === 'special' && presetId) {
    return localizedName(dict, presetId) ?? title ?? presets.find(preset => preset.id === presetId)?.name ?? null
  }

  return title
}
