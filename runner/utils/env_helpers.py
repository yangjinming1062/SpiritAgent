from collections.abc import Mapping

from .constants import get_spiritagent_home_override, get_subprocess_home


def inject_context_spiritagent_home(env: dict[str, str]) -> None:
    if value := get_spiritagent_home_override():
        env["SPIRITAGENT_HOME"] = value


def sanitize_subprocess_env(
    base_env: Mapping[str, str] | None,
    extra_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    res = dict(base_env or {})
    res.update(extra_env or {})
    inject_context_spiritagent_home(res)
    res["HOME"] = str(get_subprocess_home())
    return res
