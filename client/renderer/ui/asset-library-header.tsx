import type { ReactNode } from 'react'
import { useState } from 'react'

import { Plus, Search } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { SearchField, SettingCard } from './components'
import { BTN_PRIMARY, BTN_SUBTLE } from './palette'
import { PolicyToggleRow } from './policy-toggle-row'

/**
 * 素材库页头：政策开关区（可选）+ 说明 + 工具栏 + 筛选插槽。
 * 公共件只接数据和回调，不持有查询状态。
 */
export function AssetLibraryHeader({
  policies,
  description,
  children
}: {
  policies?: Array<{
    id: string
    label: string
    description?: string
    status?: string
    checked: boolean
    disabled: boolean
    ariaLabel: string
    onChange: () => void
  }>
  description?: string
  children: ReactNode
}): React.JSX.Element {
  return (
    <div className="space-y-4">
      {policies && policies.length > 0 && (
        <SettingCard>
          {policies.map(policy => (
            <PolicyToggleRow key={policy.id} {...policy} />
          ))}
        </SettingCard>
      )}
      {description ? <p className="text-[11px] leading-relaxed text-faint">{description}</p> : null}
      {children}
    </div>
  )
}

/**
 * 统一素材库工具栏：搜索（即时过滤或提交式）+ 筛选插槽 + 单个主创建按钮。
 * 每屏只有一个主操作（创建）；`mode` 区分搜索语义：
 * - `live`：输入即过滤，本地列表用；
 * - `submit`：回车或点搜索按钮提交，服务端搜索用。
 */
export function LibraryToolbar({
  query,
  searchLabel,
  createLabel,
  searchMode = 'live',
  createDisabled = false,
  filters,
  onQueryChange,
  onSearch,
  onCreate
}: {
  query: string
  searchLabel: string
  createLabel: string
  searchMode?: 'live' | 'submit'
  createDisabled?: boolean
  /** 筛选插槽（下拉、类型切换等），排在搜索框之后。 */
  filters?: ReactNode
  onQueryChange: (query: string) => void
  onSearch?: () => void
  onCreate: () => void
}): React.JSX.Element {
  const submitted = searchMode === 'submit'

  return (
    <form
      className="flex min-w-0 flex-wrap items-center gap-2"
      onSubmit={event => {
        event.preventDefault()

        if (submitted) {
          onSearch?.()
        }
      }}
    >
      <SearchField
        ariaLabel={searchLabel}
        className="min-w-40 max-w-none flex-1"
        onChange={onQueryChange}
        placeholder={searchLabel}
        type="search"
        value={query}
      />
      {filters}
      {submitted ? (
        <button className={cn(BTN_SUBTLE, 'shrink-0 whitespace-nowrap px-3')} type="submit">
          <Search className="size-3.5" />
          <span>{searchLabel}</span>
        </button>
      ) : null}
      <button
        className={cn(BTN_PRIMARY, 'shrink-0 whitespace-nowrap')}
        disabled={createDisabled}
        onClick={onCreate}
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
