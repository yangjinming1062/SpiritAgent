import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $systemPresetsFetched,
  fetchSystemPresets,
  presetDisplayDescription,
  presetDisplayName
} from '@/modules/conversation'
import { Globe, type IconComponent, MessageCircle, Pencil, Sparkles } from '@/shared/lib/icons'
import { BTN_PRIMARY, BTN_SUBTLE, WizardModal } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import type { SystemPresetSummary } from '@/shared/types/spiritagent'

// icon_key → Tabler 图标映射，预设选择与会话侧栏共用。新增预设须同步后端 BUILTIN_PRESETS 的 icon_key 与此表。
const PRESET_ICONS: Record<string, IconComponent> = {
  preset_companion: Sparkles,
  preset_copywriter: Pencil,
  preset_language_teacher: Globe
}

export function presetIcon(iconKey: string | null | undefined): IconComponent {
  return (iconKey && PRESET_ICONS[iconKey]) || MessageCircle
}

function PresetIcon({ iconKey }: { iconKey: string }): React.JSX.Element {
  const Icon = presetIcon(iconKey)

  return <Icon className="size-4" />
}

interface PresetPickerModalProps {
  presets: SystemPresetSummary[]
  loading: boolean
  onConfirm: (presetId: string) => void
  onClose: () => void
}

// 新建会话只列日常辅助预设；固定陪伴由单独入口承载。
export function PresetPickerModal({ presets, loading, onConfirm, onClose }: PresetPickerModalProps): React.JSX.Element {
  const [selectedId, setSelectedId] = useState<string>('')
  const fetched = useStore($systemPresetsFetched)
  const dict = useStrings()

  useEffect(() => {
    if (!fetched) {
      void fetchSystemPresets()
    }
  }, [fetched])

  const workPresets = presets.filter(p => p.id !== 'companion')
  const canSubmit = selectedId !== '' && !loading && workPresets.length > 0

  return (
    <WizardModal
      footer={
        <>
          <button className={BTN_SUBTLE} onClick={onClose} type="button">
            {dict.chat.presetPicker.cancel}
          </button>
          <button
            className={BTN_PRIMARY}
            disabled={!canSubmit}
            onClick={() => {
              if (canSubmit) {
                onConfirm(selectedId)
              }
            }}
            title={!canSubmit ? dict.chat.presetPicker.pickOne : undefined}
            type="button"
          >
            {dict.chat.presetPicker.confirm}
          </button>
        </>
      }
      onClose={onClose}
      regionId="preset-picker"
      title={dict.chat.presetPicker.title}
      widthClass="max-w-lg"
    >
      <p className="mb-3 text-[11px] leading-relaxed text-muted">{dict.chat.presetPicker.intro}</p>
      <div className="space-y-2">
        {loading || (!fetched && workPresets.length === 0) ? (
          <div className="py-6 text-center text-xs text-faint">{dict.common.loading}</div>
        ) : workPresets.length === 0 ? (
          <div className="py-6 text-center text-xs text-faint">{dict.chat.presetPicker.fetchFailed}</div>
        ) : (
          workPresets.map(p => {
            const selected = selectedId === p.id

            return (
              <button
                aria-pressed={selected}
                className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-accent-line bg-accent-soft'
                    : 'border-line-standard hover:border-line-strong hover:bg-fill-faint'
                }`}
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                type="button"
              >
                <span
                  className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg ${
                    selected ? 'bg-fill-hover text-strong' : 'bg-fill-faint text-muted'
                  }`}
                >
                  <PresetIcon iconKey={p.icon_key} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-medium text-strong">{presetDisplayName(dict, p)}</span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-muted line-clamp-2">
                    {presetDisplayDescription(dict, p)}
                  </span>
                </span>
              </button>
            )
          })
        )}
      </div>
    </WizardModal>
  )
}
