import type { ReactNode } from 'react'

import { Plus, Search } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { SearchField } from './components'
import { BTN_PRIMARY, BTN_SUBTLE } from './palette'

/**
 * 统一素材库工具栏：搜索（即时过滤或提交式）+ 筛选插槽 + 单个主创建按钮。
 * 每屏只有一个主操作（创建）；次级命令（上传、自备图）进 `secondary`，
 * 偏好开关进 `preference`，就地显示状态与短标签，不藏进菜单。`mode` 区分搜索语义：
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
  secondary,
  preference,
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
  /** 次级命令插槽（上传等），排在主按钮之后。 */
  secondary?: ReactNode
  /** 偏好开关插槽（`StatusToggle`），排在最后。 */
  preference?: ReactNode
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
      {secondary}
      {preference}
    </form>
  )
}

/**
 * 偏好开关按钮：状态点（开=绿、关=灰）加短标签，点一下直接切换，不必展开菜单。
 * 完整说明进 title，状态另由 `aria-pressed` 表达，不靠颜色单一区分。
 */
export function StatusToggle({
  label,
  title,
  checked,
  disabled = false,
  onClick
}: {
  /** 按钮上可见的短标签。 */
  label: string
  /** 悬停时的完整说明（政策名称与生效范围）。 */
  title?: string
  checked: boolean
  disabled?: boolean
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      aria-pressed={checked}
      className={cn(BTN_SUBTLE, 'shrink-0 whitespace-nowrap')}
      disabled={disabled}
      onClick={onClick}
      title={title}
      type="button"
    >
      <span
        aria-hidden="true"
        className={cn('size-1.5 shrink-0 rounded-full', checked ? 'bg-success' : 'bg-fill-trough')}
      />
      <span>{label}</span>
    </button>
  )
}
