"""窗口受理事务提交后交给共享动作编排调度。"""

from features.action_library.ports import schedule_definition

from .design import ProposalAcceptance


def schedule_accepted_proposal(acceptance: ProposalAcceptance, user_id: int) -> None:
    result = acceptance.result
    if result.outcome != "rejected" and result.proposal_id is not None:
        schedule_definition(user_id, result.proposal_id)
