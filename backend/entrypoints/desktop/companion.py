from features.companion.interaction_stats import record_interaction
from runtime.gateway_contracts import GatewaySession, RegisterMethod


def register_handlers(session: GatewaySession, register: RegisterMethod) -> None:
    async def record_stats(params: dict) -> dict:
        return await record_interaction(session.user_id, params["kind"], params["hour"])

    register("companion.record_interaction_stats", record_stats)
