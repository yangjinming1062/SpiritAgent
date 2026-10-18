"""只比较原始视觉来源；派生图、存储路径和数据库 ID 不参与视觉版本。"""

import hashlib
import json


def source_visual_key(
    identity_hash: str,
    outfit_hash: str,
    *,
    portrait_hash: str,
    identity_reference_role: str,
) -> str:
    payload = [identity_hash, portrait_hash, outfit_hash, identity_reference_role]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
