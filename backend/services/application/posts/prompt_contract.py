"""动态提示词的确定性长度与选项契约，由公共 schema 装配。"""

from components import resolve_prompt_text
from modules.companion import POST_COMMENT_MAX_CHARS, PostContentType, PostPlan


def render_post_instructions(texts: dict[str, str], language: str) -> str:
    fields = PostPlan.model_json_schema()["properties"]
    values = {
        "content_types": "/".join(item.value for item in PostContentType),
        "title_min": fields["title"]["minLength"],
        **{f"{name}_max": fields[name]["maxLength"] for name in ("title", "body", "text", "prompt", "narration")},
        "image_sizes": "/".join(fields["size"]["enum"]),
        "video_ratios": "/".join(fields["aspect_ratio"]["enum"]),
        "durations_zh": "或".join(map(str, fields["duration"]["enum"])),
        "durations_en": " or ".join(map(str, fields["duration"]["enum"])),
        **{
            f"default_{name}": fields[field]["default"]
            for name, field in (("size", "size"), ("duration", "duration"), ("ratio", "aspect_ratio"))
        },
        "comment_max": POST_COMMENT_MAX_CHARS,
    }
    result = resolve_prompt_text(texts, language)
    for name, value in values.items():
        result = result.replace("{" + name + "}", str(value))
    return result
