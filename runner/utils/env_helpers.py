from .constants import get_spiritagent_home_override, get_subprocess_home


def inject_context_spiritagent_home(env: dict) -> None:
    if value := get_spiritagent_home_override():
        env["SPIRITAGENT_HOME"] = value


def sanitize_subprocess_env(base_env: dict | None, extra_env: dict | None = None) -> dict:
    res = dict(base_env or {})
    res.update(extra_env or {})
    inject_context_spiritagent_home(res)
    res["HOME"] = str(get_subprocess_home())
    return res
