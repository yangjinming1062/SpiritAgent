import type { SkillItem } from '@ipc/contracts'
import { useEffect, useMemo, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { cn } from '@/shared/lib/utils'
import {
  CHIP_FILTER,
  CHIP_FILTER_ACTIVE,
  EmptyState,
  LoadingBlock,
  SearchField,
  SECTION_TITLE,
  SettingCard,
  SettingRow,
  SettingsSectionIntro,
  Toggle
} from '@/shared/panel'
import { refreshSession } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

function categoryLabel(key: string): string {
  return key.replace(/-/g, ' ')
}

export function SkillsPage(): React.JSX.Element {
  const t = useStrings()
  const s = t.settings.skills
  const sk = t.skills
  const brandName = t.brand.name
  const loadErrorLabel = s.loadError
  const loadErrorLabelRef = useLatestRef(loadErrorLabel)

  const loader = useAsyncLoader<SkillItem[]>(async () => {
    const res = await window.spiritagent.skills.list()

    if (!res.ok) {
      notifyError(res.error ?? 'load-failed', loadErrorLabelRef.current)

      throw new Error(res.error ?? 'skills list failed')
    }

    return res.skills ?? []
  })

  const [skills, setSkills] = useState<SkillItem[]>([])
  const loading = loader.isLoading
  const loadFailed = loader.error !== null

  // loader.data 同步到本地状态——挂载时 loader 是真相源，本地写入之后优先
  useEffect(() => {
    if (loader.data) {
      setSkills(loader.data)
    }
  }, [loader.data])

  const [searchTerm, setSearchTerm] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  const saveErrorRef = useLatestRef(s.saveError)
  const refreshErrorRef = useLatestRef(s.refreshError)

  // 开关不做乐观更新，列表只取主进程返回的全量结果；失败时界面仍是点击前的状态，只需提示。
  const toggle = async (name: string, nextEnabled: boolean) => {
    try {
      const res = await window.spiritagent.skills.setEnabled({ name, enabled: nextEnabled })

      if (!res.ok || !res.skills) {
        notifyError(res.error ?? 'save-failed', saveErrorRef.current)

        return
      }

      setSkills(res.skills)

      // 刷新 JWT 以获取新 skill 权限；不关精灵窗口的 WS（权限变更在服务端完成）
      try {
        await refreshSession()
      } catch (err) {
        notifyError(err, refreshErrorRef.current)
      }
    } catch (err) {
      notifyError(err, saveErrorRef.current)
    }
  }

  const { counts, orderedCategories, groupedVisible, visibleCount } = useMemo(() => {
    const counts = new Map<string, number>()
    const needle = searchTerm.trim().toLowerCase()
    const groupedVisible = new Map<string, SkillItem[]>()
    let visibleCount = 0

    // 与平台不兼容的技能不显示；main/ipc/skills.ts 拒绝启用它们。
    for (const skill of skills) {
      if (!skill.compatible) {
        continue
      }

      visibleCount += 1
      const key = skill.category
      counts.set(key, (counts.get(key) ?? 0) + 1)

      if (selectedCategory !== null && key !== selectedCategory) {
        continue
      }

      if (needle) {
        const matches =
          skill.name.toLowerCase().includes(needle) ||
          (skill.description ?? '').toLowerCase().includes(needle) ||
          skill.category.toLowerCase().includes(needle)

        if (!matches) {
          continue
        }
      }

      const list = groupedVisible.get(key)

      if (list) {
        list.push(skill)
      } else {
        groupedVisible.set(key, [skill])
      }
    }

    const orderedCategories = Array.from(counts.keys()).sort((a, b) => a.localeCompare(b))

    return { counts, orderedCategories, groupedVisible, visibleCount }
  }, [skills, searchTerm, selectedCategory])

  if (loading) {
    return <LoadingBlock label={s.loading} />
  }

  const noSkillsAtAll = skills.length === 0
  const showLoadFailed = loadFailed && noSkillsAtAll
  const showEmptyInstall = !loadFailed && noSkillsAtAll
  const allHiddenByPlatform = !noSkillsAtAll && visibleCount === 0
  const showFilterEmpty = !noSkillsAtAll && !allHiddenByPlatform && groupedVisible.size === 0

  let body: React.ReactNode

  if (showLoadFailed) {
    body = <EmptyState description={sk.loadFailedDesc} title={sk.loadFailedTitle} />
  } else if (showEmptyInstall) {
    body = <EmptyState description={s.emptyDesc(brandName)} title={s.emptyTitle} />
  } else if (allHiddenByPlatform) {
    body = <EmptyState description={s.hiddenByPlatformDesc(brandName)} title={s.hiddenByPlatformTitle} />
  } else if (showFilterEmpty) {
    body = <EmptyState description={sk.noSkillsDesc} title={sk.noSkillsTitle} />
  } else if (selectedCategory !== null) {
    const selectedSkills = groupedVisible.get(selectedCategory) ?? []

    body = (
      <SettingCard>
        {selectedSkills.map(skill => (
          <SettingRow description={skill.description || sk.noDescription} key={skill.name} label={skill.name}>
            <Toggle ariaLabel={skill.name} checked={skill.enabled} onChange={value => void toggle(skill.name, value)} />
          </SettingRow>
        ))}
      </SettingCard>
    )
  } else {
    body = (
      <div className="flex flex-col gap-6">
        {orderedCategories.flatMap(categoryKey => {
          const items = groupedVisible.get(categoryKey)

          return items
            ? [
                <div className="flex flex-col gap-2" key={categoryKey}>
                  <h3 className={cn(SECTION_TITLE, 'capitalize')}>{categoryLabel(categoryKey)}</h3>
                  <SettingCard>
                    {items.map(skill => (
                      <SettingRow
                        description={skill.description || sk.noDescription}
                        key={skill.name}
                        label={skill.name}
                      >
                        <Toggle
                          ariaLabel={skill.name}
                          checked={skill.enabled}
                          onChange={value => void toggle(skill.name, value)}
                        />
                      </SettingRow>
                    ))}
                  </SettingCard>
                </div>
              ]
            : []
        })}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={s.intro} title={s.title} />
      {!noSkillsAtAll && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              className={selectedCategory === null ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
              onClick={() => setSelectedCategory(null)}
              type="button"
            >
              {sk.all} · {visibleCount}
            </button>
            {orderedCategories.map(categoryKey => (
              <button
                className={selectedCategory === categoryKey ? CHIP_FILTER_ACTIVE : CHIP_FILTER}
                key={categoryKey}
                onClick={() => setSelectedCategory(categoryKey)}
                type="button"
              >
                <span className="capitalize">{categoryLabel(categoryKey)}</span> · {counts.get(categoryKey) ?? 0}
              </button>
            ))}
          </div>
          <div>
            <SearchField
              aria-label={sk.searchSkills}
              onChange={setSearchTerm}
              placeholder={sk.searchSkills}
              value={searchTerm}
            />
          </div>
        </div>
      )}
      <div>{body}</div>
    </div>
  )
}
