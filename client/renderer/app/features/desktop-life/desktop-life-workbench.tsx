import type { ReactNode } from 'react'
import { useState } from 'react'

import type { DesktopVideoAction, DesktopVideoSet } from '@/modules/desktop-videos'
import { Plus } from '@/shared/lib/icons'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, PanelSelect, SearchField } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { DesktopLifeHeader } from './desktop-life-header'
import { DesktopLifeProposalList } from './desktop-life-proposals'
import { desktopLifeActionName, desktopLifeActionStatus } from './desktop-life-status'
import styles from './desktop-life.module.css'

interface DesktopLifeWorkbenchProps {
  set: DesktopVideoSet
  action: DesktopVideoAction | undefined
  busy: boolean
  canAdd: boolean
  canCustomize: boolean
  onBack: () => void
  onActivate: () => void
  onMore: () => void
  onAdd: () => void
  onCustomize: () => void
  onSelect: (action: DesktopVideoAction) => void
  children: ReactNode
}

export function DesktopLifeWorkbench({
  set,
  action,
  busy,
  canAdd,
  canCustomize,
  onBack,
  onActivate,
  onMore,
  onAdd,
  onCustomize,
  onSelect,
  children
}: DesktopLifeWorkbenchProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.desktopLife
  const common = useStrings().common
  const [query, setQuery] = useState('')
  const idle = set.actions.find(item => item.key === 'idle')

  const actions = set.actions.filter(
    item =>
      item.key === 'idle' ||
      item.definition_id !== null ||
      item.current ||
      item.draft ||
      item.status === 'failed' ||
      item.id === action?.id
  )

  const orderedActions = [
    ...actions.filter(item => item.key === 'idle'),
    ...actions.filter(item => item.key !== 'idle')
  ]

  const visibleActions = orderedActions.filter(item =>
    desktopLifeActionName(item, t).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  )

  // 还没产出动作的设计提案按背景展示，不占用动作列表的选择位。
  const pendingProposals = set.proposals.filter(proposal => !proposal.action_id)

  const status = (item: DesktopVideoAction): string =>
    desktopLifeActionStatus(item, t, common.videoQueue, common.actionVariants)

  const addControls = (
    <>
      {canAdd && (
        <button className={BTN_SUBTLE} onClick={onAdd} type="button">
          <Plus className="size-4" />
          {t.design}
        </button>
      )}
      {canCustomize && (
        <button className={BTN_GHOST} onClick={onCustomize} type="button">
          {t.customAction}
        </button>
      )}
    </>
  )

  return (
    <section className={styles.workbench}>
      <DesktopLifeHeader onBack={onBack} title={set.title.trim() || t.newSceneName}>
        {set.is_current ? (
          <span className={styles.badge}>{t.current}</span>
        ) : idle?.playable ? (
          <button className={BTN_PRIMARY} disabled={busy} onClick={onActivate} type="button">
            {idle.draft ? t.useCurrent : t.play}
          </button>
        ) : null}
        <button className={BTN_SUBTLE} onClick={onMore} type="button">
          {t.more}
        </button>
      </DesktopLifeHeader>
      <div className={styles.workbenchBody}>
        <aside aria-label={t.allActions} className={styles.actionSidebar}>
          <div className={styles.actionListHeader}>
            <h3 className="text-xs font-medium text-strong">{t.allActions}</h3>
            <SearchField
              ariaLabel={t.searchActions}
              className="max-w-none"
              onChange={setQuery}
              placeholder={t.searchActions}
              value={query}
            />
          </div>
          <div className={styles.actionList}>
            {visibleActions.map(item => (
              <button
                aria-pressed={action?.id === item.id}
                className={styles.actionOption}
                key={item.id}
                onClick={() => onSelect(item)}
                type="button"
              >
                <span className={styles.actionName}>{desktopLifeActionName(item, t)}</span>
                <span className={styles.actionStatus}>{status(item)}</span>
              </button>
            ))}
            {visibleActions.length === 0 && <p className="px-3 py-4 text-xs text-muted">{t.noMatchingActions}</p>}
          </div>
          {(canAdd || canCustomize) && <div className={styles.actionListFooter}>{addControls}</div>}
        </aside>
        <div className={styles.compactActionPicker}>
          <PanelSelect
            ariaLabel={t.chooseAction}
            onChange={value => {
              const next = orderedActions.find(item => String(item.id) === value)

              if (next) {
                onSelect(next)
              }
            }}
            options={orderedActions.map(item => ({
              value: String(item.id),
              label: `${desktopLifeActionName(item, t)} · ${status(item)}`
            }))}
            value={action ? String(action.id) : ''}
            widthClass="min-w-0 flex-1"
          />
          {addControls}
        </div>
        <div className={styles.actionPane}>
          <DesktopLifeProposalList proposals={pendingProposals} />
          {children}
        </div>
      </div>
    </section>
  )
}
