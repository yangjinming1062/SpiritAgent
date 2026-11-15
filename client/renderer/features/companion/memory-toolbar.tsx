import type React from 'react'

import { presetDisplayName } from '@/features/conversation'
import { useStrings } from '@/shared/strings'
import { CapsuleTabs, Disclosure, HINT_TEXT, PanelSelect, SearchField } from '@/ui'
import type { CompanionMemoryBrowser } from '@client-shared/ipc/ui-state'
import type { MemoryCounts, SystemPresetSummary } from '@protocol'

export type MemorySectionId = 'recall' | 'profile'
export type MemoryStatusFilter = CompanionMemoryBrowser['status']

/** 命令栏第一行：内容类型、维护说明与记忆作用域；两种内容类型共用这一行。 */
export function MemoryToolbar({
  onChangePreset,
  onChangeSection,
  presetId,
  presets,
  section
}: {
  onChangePreset: (id: string) => void
  onChangeSection: (id: MemorySectionId) => void
  presetId: string
  presets: readonly SystemPresetSummary[]
  section: MemorySectionId
}): React.JSX.Element {
  const dict = useStrings()
  const persona = dict.settings.persona
  const t = dict.settings.memory
  const scoped = presetId === 'companion'

  return (
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
      {scoped ? (
        <CapsuleTabs
          ariaLabel={persona.memoryScopeAria}
          onChange={onChangeSection}
          options={[
            { label: persona.memoryKindRecall, value: 'recall' },
            { label: t.profile.title, value: 'profile' }
          ]}
          size="sm"
          value={section}
          variant="segmented"
        />
      ) : (
        <p className={HINT_TEXT}>{t.otherPresetHint}</p>
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {section === 'recall' ? (
          <Disclosure
            bodyClassName="max-w-64"
            chevron
            summaryClassName="text-[11px] text-muted"
            title={t.maintenanceTitle}
            variant="inline"
          >
            <p className={HINT_TEXT}>{t.maintenanceHint}</p>
          </Disclosure>
        ) : null}
        {presets.length > 1 ? (
          <PanelSelect
            ariaLabel={t.presetLabel}
            onChange={onChangePreset}
            options={presets.map(preset => ({ label: presetDisplayName(dict, preset), value: preset.id }))}
            value={presetId}
            widthClass="w-44"
          />
        ) : null}
      </div>
    </div>
  )
}

/** 命令栏第二行：只在「记住的事」出现，搜索与状态计数分桶放在一起。 */
export function MemoryRecallFilters({
  counts,
  onChangeQuery,
  onChangeStatus,
  query,
  status
}: {
  counts: MemoryCounts | null
  onChangeQuery: (query: string) => void
  onChangeStatus: (status: MemoryStatusFilter) => void
  query: string
  status: MemoryStatusFilter
}): React.JSX.Element {
  const dict = useStrings()
  const persona = dict.settings.persona
  const t = dict.settings.memory

  const options: ReadonlyArray<{ label: string; value: MemoryStatusFilter }> = [
    { label: t.tabActive(counts?.active ?? '…'), value: 'active' },
    { label: t.tabCandidate(counts?.candidate ?? '…'), value: 'candidate' },
    { label: t.tabInvalidated(counts?.invalidated ?? '…'), value: 'invalidated' },
    { label: t.tabExpired(counts?.expired ?? '…'), value: 'expired' }
  ]

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2">
      <SearchField
        ariaLabel={persona.memorySearchAria}
        className="min-w-40 max-w-none flex-1"
        onChange={onChangeQuery}
        placeholder={persona.memorySearchPlaceholder}
        type="search"
        value={query}
      />
      <CapsuleTabs ariaLabel={t.tabAriaLabel} onChange={onChangeStatus} options={options} size="sm" value={status} />
    </div>
  )
}
