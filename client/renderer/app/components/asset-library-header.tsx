import type { ReactNode, Ref } from 'react'
import { useState } from 'react'

import { Plus, Search } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, SettingCard, Toggle } from '@/shared/panel'

interface LibraryPolicy {
  id: string
  label: string
  description: string
  status: string
  checked: boolean
  disabled: boolean
  ariaLabel: string
  onChange: () => void
}

export function AssetLibraryHeader({
  policies,
  description,
  children
}: {
  policies: LibraryPolicy[]
  description?: string
  children: ReactNode
}): React.JSX.Element {
  return (
    <div className="space-y-4">
      {policies.length > 0 && (
        <SettingCard>
          {policies.map(policy => (
            <div className="flex items-center justify-between gap-4 p-4" key={policy.id}>
              <div className="min-w-0">
                <p className="text-sm font-medium text-strong">{policy.label}</p>
                <p className="mt-1 text-xs leading-relaxed text-muted">{policy.description}</p>
                <p aria-live="polite" className="mt-1 text-xs text-muted">
                  {policy.status}
                </p>
              </div>
              <Toggle
                ariaLabel={policy.ariaLabel}
                checked={policy.checked}
                disabled={policy.disabled}
                onChange={policy.onChange}
              />
            </div>
          ))}
        </SettingCard>
      )}
      {description && <p className="text-[11px] leading-relaxed text-faint">{description}</p>}
      {children}
    </div>
  )
}

export function AssetLibraryToolbar({
  query,
  searchLabel,
  searchButtonLabel,
  createLabel,
  createDisabled = false,
  createRef,
  onQueryChange,
  onSearch,
  onCreate
}: {
  query: string
  searchLabel: string
  searchButtonLabel?: string
  createLabel: string
  createDisabled?: boolean
  createRef?: Ref<HTMLButtonElement>
  onQueryChange: (query: string) => void
  onSearch?: () => void
  onCreate: () => void
}): React.JSX.Element {
  return (
    <form
      className="flex min-w-0 flex-wrap gap-2"
      onSubmit={event => {
        event.preventDefault()
        onSearch?.()
      }}
    >
      <input
        aria-label={searchLabel}
        className={cn(INPUT_CLASS, 'min-w-24 flex-1')}
        onChange={event => onQueryChange(event.target.value)}
        placeholder={searchLabel}
        type="search"
        value={query}
      />
      {onSearch && searchButtonLabel ? (
        <button className={cn(BTN_SUBTLE, 'shrink-0 whitespace-nowrap px-3')} type="submit">
          <Search className="size-3.5" />
          <span>{searchButtonLabel}</span>
        </button>
      ) : null}
      <button
        className={cn(BTN_PRIMARY, 'shrink-0 whitespace-nowrap')}
        disabled={createDisabled}
        onClick={onCreate}
        ref={createRef}
        type="button"
      >
        <Plus className="size-3.5" />
        <span>{createLabel}</span>
      </button>
    </form>
  )
}

// 搜索保留在页面所有者中，查看详情或创建后返回仍使用原筛选。
// 本地列表按输入即时过滤，输入词与生效筛选始终一致；服务端搜索由调用方自行提交。
export function useAssetLibrarySearch() {
  const [query, setQuery] = useState('')
  const term = query.trim().toLocaleLowerCase()

  return {
    query,
    searching: term.length > 0,
    onQueryChange: (next: string): void => setQuery(next),
    matches: (...values: (string | null | undefined)[]): boolean =>
      !term || values.some(value => value?.toLocaleLowerCase().includes(term))
  }
}

export type AssetLibrarySearch = ReturnType<typeof useAssetLibrarySearch>
