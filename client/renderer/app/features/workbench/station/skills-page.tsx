import { useMemo, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
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
import type { SkillItem } from '@ipc/contracts'

const EMPTY_SKILLS: SkillItem[] = []

function categoryLabel(key: string): string {
  return key.replace(/-/g, ' ')
}

export function SkillsPage(): React.JSX.Element {
  const t = useStrings()
  const s = t.settings.skills
  const sk = t.skills
  const brandName = t.brand.name

  const loader = useAsyncLoader<SkillItem[]>(async () => {
    const res = await window.spiritagent.skills.list()

    if (!res.ok) {
      notifyError(res.error ?? 'load-failed', s.loadError)

      throw new Error(res.error ?? 'skills list failed')
    }

    return res.skills ?? []
  })

  const skills = loader.data ?? EMPTY_SKILLS
  const loading = loader.isLoading
  const loadFailed = loader.error !== null

  const [searchTerm, setSearchTerm] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  // 开关不做乐观更新，列表只取主进程返回的全量结果；失败时界面仍是点击前的状态，只需提示。
  const toggle = async (name: string, nextEnabled: boolean) => {
    try {
      const res = await window.spiritagent.skills.setEnabled({ name, enabled: nextEnabled })

      if (!res.ok || !res.skills) {
        notifyError(res.error ?? 'save-failed', s.saveError)

        return
      }

      loader.setData(res.skills)

      // 刷新 JWT 以获取新 skill 权限；不关精灵窗口的 WS（权限变更在服务端完成）
      try {
        await refreshSession()
      } catch (err) {
        notifyError(err, s.refreshError)
      }
    } catch (err) {
      notifyError(err, s.saveError)
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
  } else {
    // 选中类别时 groupedVisible 只含该类，只有「全部」才需要类别标题。
    body = (
      <div className="flex flex-col gap-6">
        {orderedCategories.map(categoryKey => {
          const items = groupedVisible.get(categoryKey)

          if (!items) {
            return null
          }

          return (
            <div className="flex flex-col gap-2" key={categoryKey}>
              {selectedCategory === null && (
                <h3 className={cn(SECTION_TITLE, 'capitalize')}>{categoryLabel(categoryKey)}</h3>
              )}
              <SettingCard>
                {items.map(skill => (
                  <SettingRow description={skill.description || sk.noDescription} key={skill.name} label={skill.name}>
                    <Toggle
                      ariaLabel={skill.name}
                      checked={skill.enabled}
                      onChange={value => void toggle(skill.name, value)}
                    />
                  </SettingRow>
                ))}
              </SettingCard>
            </div>
          )
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
