import type { ToolsetItem } from '@ipc/contracts'
import { useMemo, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { TOOLSET_CATALOG, type ToolsetCatalogEntry } from '@/shared/lib/toolset-catalog'
import { EmptyState, LoadingBlock, Pill, SearchField, SettingCard, Toggle } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

type ToolsetView = {
  catalog: ToolsetCatalogEntry
  label: string
  description: string
  roster: ToolsetItem | undefined
}

const EMPTY_TOOLSETS: ToolsetItem[] = []

export function ToolsetsPage(): React.JSX.Element {
  const t = useStrings()
  const sk = t.skills
  const toolsetText = t.toolsets

  const loader = useAsyncLoader<ToolsetItem[]>(async () => {
    const res = await window.spiritagent.toolsets.list()

    if (!res.ok) {
      notifyError(res.error ?? 'load-failed', sk.toolsetsLoadFailed)

      throw new Error(res.error ?? 'toolsets list failed')
    }

    return res.toolsets ?? []
  })

  const toolsets = loader.data ?? EMPTY_TOOLSETS
  const loading = loader.isLoading
  const loadFailed = loader.error !== null

  const [searchTerm, setSearchTerm] = useState('')
  const [savingId, setSavingId] = useState<string | null>(null)

  const rosterById = useMemo(() => {
    const map = new Map<string, ToolsetItem>()

    for (const t of toolsets) {
      map.set(t.id, t)
    }

    return map
  }, [toolsets])

  const visibleEntries = useMemo(() => {
    const needle = searchTerm.trim().toLowerCase()

    return TOOLSET_CATALOG.flatMap<ToolsetView>(entry => {
      const roster = rosterById.get(entry.id)

      const texts = (toolsetText as Record<string, { description: string; label: string }>)[entry.id] ?? {
        description: '',
        label: entry.id
      }

      const toolNames = roster?.toolNames ?? []

      const matches =
        !needle ||
        entry.id.toLowerCase().includes(needle) ||
        texts.label.toLowerCase().includes(needle) ||
        texts.description.toLowerCase().includes(needle) ||
        toolNames.some(n => n.toLowerCase().includes(needle))

      return matches ? [{ catalog: entry, label: texts.label, description: texts.description, roster }] : []
    })
  }, [rosterById, searchTerm, toolsetText])

  const enabledCount = useMemo(() => toolsets.filter(t => t.enabled).length, [toolsets])

  // 开关不做乐观更新，列表只取主进程返回的全量结果；失败时界面仍是点击前的状态，只需提示。
  const toggle = async (id: string, nextEnabled: boolean) => {
    setSavingId(id)

    try {
      const res = await window.spiritagent.toolsets.setEnabled({ id, enabled: nextEnabled })

      if (!res.ok || !res.toolsets) {
        notifyError(res.error ?? 'save-failed', sk.toolsetsSaveFailed)

        return
      }

      loader.setData(res.toolsets)
    } catch (err) {
      notifyError(err, sk.toolsetsSaveFailed)
    } finally {
      setSavingId(null)
    }
  }

  if (loading) {
    return <LoadingBlock label={sk.loading} />
  }

  if (loadFailed && toolsets.length === 0) {
    return <EmptyState description={sk.toolsetsLoadFailedDesc} title={sk.toolsetsLoadFailed} />
  }

  const hasEntries = visibleEntries.length > 0

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        {hasEntries && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Pill tone="primary">{sk.toolsetsEnabled(enabledCount, toolsets.length)}</Pill>
          </div>
        )}
        <div>
          <SearchField
            aria-label={sk.searchToolsets}
            onChange={setSearchTerm}
            placeholder={sk.searchToolsets}
            value={searchTerm}
          />
        </div>
      </div>

      {hasEntries ? (
        <div className="flex flex-col gap-3">
          {visibleEntries.map(({ catalog, label, description, roster }) => {
            const Icon = catalog.icon
            const enabled = roster?.enabled ?? true
            const toolNames = roster?.toolNames ?? []

            return (
              <SettingCard className="p-4" divided={false} key={catalog.id}>
                <div className="flex items-start gap-3">
                  <Icon className="mt-0.5 size-5 shrink-0 text-muted" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-strong">{label || catalog.id}</div>
                    <div className="mt-1 text-[11px] leading-relaxed text-muted">{description}</div>
                  </div>
                  <Toggle
                    ariaLabel={label || catalog.id}
                    checked={enabled}
                    disabled={savingId === catalog.id}
                    onChange={value => void toggle(catalog.id, value)}
                  />
                </div>
                {toolNames.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5 pl-8">
                    {toolNames.map(name => (
                      <span
                        className="rounded-md border border-line-standard bg-fill-faint px-1.5 py-0.5 font-mono text-[0.65rem] text-muted"
                        key={name}
                      >
                        {name}
                      </span>
                    ))}
                  </div>
                )}
              </SettingCard>
            )
          })}
        </div>
      ) : (
        <EmptyState description={sk.noToolsetsDesc} title={sk.noToolsetsTitle} />
      )}
    </div>
  )
}
