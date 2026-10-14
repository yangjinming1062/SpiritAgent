from .context import current_context


def is_interrupted() -> bool:
    return current_context().cancel_event.is_set()
