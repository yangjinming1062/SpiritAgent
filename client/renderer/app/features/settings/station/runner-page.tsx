import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'

import { useAutoSave } from '@/shared/hooks/use-auto-save'
import { cn } from '@/shared/lib/utils'
import {
  CapsuleTabs,
  EmptyState,
  HINT_TEXT,
  INPUT_CLASS,
  LoadingBlock,
  SECTION_TITLE,
  SettingCard,
  SettingRow,
  SettingsSectionIntro,
  Toggle
} from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { getIn, setIn, useRunnerConfig } from './use-runner-config'

type SelectRow = {
  kind: 'select'
  path: readonly string[]
  title: string
  options: readonly { value: string; label: string }[]
  default: string
}

type SwitchRow = {
  kind: 'switch'
  path: readonly string[]
  title: string
}

type InputRow = {
  kind: 'input'
  type?: 'text' | 'number' | 'password'
  path: readonly string[]
  title: string
  default: string | number
}

type Row = SelectRow | SwitchRow | InputRow

function readInputValue(row: InputRow, raw: string): string | number {
  if (row.type !== 'number') {
    return raw
  }

  // Number('') 是 0 而不是 NaN ——把空输入视为无效，回退到默认值，而不是悄悄写入 0。
  if (raw === '') {
    return row.default
  }

  const parsed = Number(raw)

  return Number.isNaN(parsed) ? row.default : parsed
}

export function RunnerPage(): React.JSX.Element {
  const dict = useStrings()
  const r = dict.settings.runner

  const { config, setConfig, isLoading, patch } = useRunnerConfig(r.failedLoad)
  // 按路径记录本页改过的字段，只提交对应点键，避免覆盖其他来源的配置更新。
  const [dirtyPaths, setDirtyPaths] = useState<ReadonlyMap<string, readonly string[]>>(new Map())
  const dirtyPathsRef = useRef(dirtyPaths)
  const revisionRef = useRef(0)
  const mountedRef = useRef(true)
  dirtyPathsRef.current = dirtyPaths

  useEffect(
    () => () => {
      mountedRef.current = false
    },
    []
  )

  const persist = async (snapshot: Record<string, unknown>): Promise<void> => {
    const revision = revisionRef.current

    for (const path of dirtyPathsRef.current.values()) {
      await patch(path, getIn(snapshot, path))
    }

    if (mountedRef.current && revisionRef.current === revision) {
      setDirtyPaths(new Map())
    }
  }

  const { status: saveStatus } = useAutoSave({
    dirty: dirtyPaths.size > 0,
    onError: error => notifyError(error, r.saveFailed),
    onSave: persist,
    value: config ?? {}
  })

  const updateField = useCallback(
    (path: readonly string[], value: unknown) => {
      setConfig(prev => (prev ? setIn(prev, path, value) : prev))
      revisionRef.current += 1
      setDirtyPaths(prev => new Map(prev).set(path.join('.'), path))
    },
    [setConfig]
  )

  const envType = ((getIn(config, ['terminal', 'env_type']) as string) || 'local').toLowerCase()

  const rowGroups = useMemo<readonly { heading: string; rows: readonly Row[] }[]>(() => {
    const groups: { heading: string; rows: readonly Row[] }[] = [
      {
        heading: r.terminal,
        rows: [
          {
            kind: 'select',
            path: ['terminal', 'env_type'],
            title: r.terminalEnvType,
            options: [
              { value: 'local', label: r.envLocal },
              { value: 'ssh', label: 'SSH' }
            ],
            default: 'local'
          }
        ]
      }
    ]

    if (envType === 'ssh') {
      groups.push({
        heading: r.ssh,
        rows: [
          { kind: 'input', path: ['terminal', 'ssh', 'host'], title: r.sshHost, default: '' },
          { kind: 'input', type: 'number', path: ['terminal', 'ssh', 'port'], title: r.sshPort, default: 22 },
          { kind: 'input', path: ['terminal', 'ssh', 'user'], title: r.sshUser, default: '' },
          { kind: 'input', type: 'password', path: ['terminal', 'ssh', 'password'], title: r.sshPassword, default: '' },
          { kind: 'input', path: ['terminal', 'ssh', 'key'], title: r.sshKey, default: '' }
        ]
      })
    }

    groups.push(
      {
        heading: r.browser,
        rows: [{ kind: 'switch', path: ['browser', 'allow_private_urls'], title: r.browserAllowPrivateUrls }]
      },
      {
        heading: r.security,
        rows: [{ kind: 'switch', path: ['security', 'redact_secrets'], title: r.securityRedactSecrets }]
      }
    )

    return groups
  }, [envType, r])

  if (isLoading) {
    return <LoadingBlock label={r.loading} />
  }

  if (!config) {
    return <EmptyState title={r.failedLoad} />
  }

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={r.intro} title={r.title} />

      <div className="space-y-6">
        {rowGroups.map(group => (
          <section key={group.heading}>
            <p className={cn(SECTION_TITLE, 'mb-2')}>{group.heading}</p>
            <SettingCard>
              {group.rows.map(row => (
                <SettingRow key={row.title} label={row.title}>
                  {renderRowAction(row, config, updateField, false)}
                </SettingRow>
              ))}
            </SettingCard>
          </section>
        ))}
      </div>

      <div className="flex min-h-5 justify-end pt-2">
        {saveStatus === 'saving' && <span className={HINT_TEXT}>{dict.common.saving}</span>}
        {saveStatus === 'saved' && <span className={HINT_TEXT}>{r.saveSuccess}</span>}
        {saveStatus === 'error' && <span className="text-[10px] leading-relaxed text-danger-fg">{r.saveFailed}</span>}
      </div>
    </div>
  )
}

function renderRowAction(
  row: Row,
  config: Record<string, unknown>,
  updateField: (path: readonly string[], value: unknown) => void,
  disabled: boolean
): React.ReactNode {
  if (row.kind === 'select') {
    const rawValue = (getIn(config, row.path) as string) || row.default

    const matchedOption = row.options.find(
      opt => opt.value === rawValue || opt.value.toLowerCase() === rawValue.toLowerCase()
    )

    const currentValue = matchedOption ? matchedOption.value : row.default

    return (
      <CapsuleTabs
        ariaLabel={row.title}
        disabled={disabled}
        onChange={v => updateField(row.path, v)}
        options={row.options}
        size="sm"
        value={currentValue}
      />
    )
  }

  if (row.kind === 'switch') {
    return (
      <Toggle
        ariaLabel={row.title}
        checked={!!getIn(config, row.path)}
        disabled={disabled}
        onChange={v => updateField(row.path, v)}
      />
    )
  }

  // row.kind === 'input'
  return (
    <input
      aria-label={row.title}
      className={cn(INPUT_CLASS, 'w-36')}
      disabled={disabled}
      onChange={e => updateField(row.path, readInputValue(row, e.target.value))}
      type={row.type || 'text'}
      value={(getIn(config, row.path) as string | number) ?? row.default}
    />
  )
}
