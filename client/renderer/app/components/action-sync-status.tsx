import type React from 'react'

import { useStrings } from '@/shared/strings'

import type { ApiSchemas } from '../../../../shared/api.generated'

interface ActionSyncStatusProps {
  targets: ApiSchemas['ActionVariantSummary'][]
}

export function ActionSyncStatus({ targets }: ActionSyncStatusProps): React.JSX.Element | null {
  const t = useStrings().common.actionVariants

  if (targets.length === 0) {
    return null
  }

  return (
    <div className="shrink-0 rounded-lg border border-line-hairline p-3">
      <p className="text-xs font-medium text-strong">{t.title}</p>
      <ul className="mt-2 max-h-32 space-y-2 overflow-y-auto">
        {targets.map(target => (
          <li className="text-xs" key={`${target.target_kind}:${target.target_id}`}>
            <div className="flex items-start justify-between gap-3">
              <span className="min-w-0 break-words text-body">
                {t.kinds[target.target_kind]} · {target.label}
              </span>
              <span className="shrink-0 text-muted">
                {target.has_ready_asset && !['ready', 'disabled', 'excluded'].includes(target.status)
                  ? t.readyWithStatus(target.status === 'failed' ? t.remakeFailed : t.statuses[target.status])
                  : t.statuses[target.status]}
              </span>
            </div>
            {target.reason ? <p className="mt-1 break-words text-[11px] text-muted">{target.reason}</p> : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
