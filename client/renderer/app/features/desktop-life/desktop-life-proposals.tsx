import type { DesktopVideoProposal } from '@/modules/desktop-videos'
import { useStrings } from '@/shared/strings'

import styles from './desktop-life.module.css'

// 已提交但还没产出动作的设计提案常驻展示：状态与原因一直可见，
// 产出动作后由调用方按 action_id 过滤掉，不会重复占位。
export function DesktopLifeProposalList({
  proposals
}: {
  proposals: DesktopVideoProposal[]
}): React.JSX.Element | null {
  const strings = useStrings()
  const t = strings.desktopLife

  if (proposals.length === 0) {
    return null
  }

  return (
    <div className={styles.proposalGroup}>
      <p className={styles.proposalGroupTitle}>{t.proposalGroup}</p>
      {proposals.map(proposal => (
        <div className={styles.proposalItem} key={proposal.proposal_id}>
          <span className={styles.actionName}>{proposal.name.trim() || proposal.description.trim()}</span>
          <span className={styles.actionStatus}>
            {t.proposalStatuses[proposal.status as keyof typeof t.proposalStatuses] || t.preparing}
          </span>
          {proposal.review_reason ? <span className={styles.proposalReason}>{proposal.review_reason}</span> : null}
        </div>
      ))}
    </div>
  )
}
