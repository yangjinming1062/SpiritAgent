import type { ReactNode } from 'react'

import { Plus, Search } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { SearchField } from './components'
import { BTN_PRIMARY, BTN_SUBTLE } from './palette'

/**
 * 统一素材库工具栏：搜索（即时过滤或提交式）+ 筛选插槽 + 单个主创建按钮。
 * 每屏只有一个主操作（创建）；次级命令（上传、自备图）进 `secondary`，
 * 偏好开关与说明进 `overflow`，不再纵向占节。`mode` 区分搜索语义：
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
  overflow,
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
  /** 溢出菜单插槽（偏好、说明等），排在最后。 */
  overflow?: ReactNode
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
      {overflow}
    </form>
  )
}
