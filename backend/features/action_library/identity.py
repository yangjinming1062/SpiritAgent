"""区分动作归属与本次评审参考；画面变化不改变来源归属。"""

import hashlib
import json

from .schemas import ActionDefinitionSnapshot, ActionSourceSnapshot


def definition_applies_to_source(definition: ActionDefinitionSnapshot, source: ActionSourceSnapshot) -> bool:
    return (
        definition.user_id == source.user_id
        and definition.scope == source.scope
        and definition.outfit_id == source.outfit_id
        and (
            definition.scope == "outfit"
            or (definition.origin_kind, definition.origin_target_id) == (source.target_kind, source.target_id)
        )
    )


def definition_matches_source(definition: ActionDefinitionSnapshot, source: ActionSourceSnapshot) -> bool:
    return definition_applies_to_source(definition, source) and definition.source_visual_key == source.source_visual_key


def same_review_reference(expected: ActionSourceSnapshot, current: ActionSourceSnapshot) -> bool:
    return (
        expected.user_id == current.user_id
        and expected.scope == current.scope
        and expected.source_visual_key == current.source_visual_key
        and (expected.target_kind, expected.target_id) == (current.target_kind, current.target_id)
        and expected.reference_paths == current.reference_paths
        and expected.reference_roles == current.reference_roles
        and expected.outfit_description == current.outfit_description
    )


def source_visual_key(
    identity_hash: str,
    outfit_hash: str,
    *,
    portrait_hash: str,
    identity_reference_role: str,
) -> str:
    payload = [identity_hash, portrait_hash, outfit_hash, identity_reference_role]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
