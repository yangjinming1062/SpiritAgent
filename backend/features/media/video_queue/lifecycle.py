"""队列先恢复远端占用，再开放各业务的制作恢复。"""

from runtime.lifecycle import RuntimeParticipant

from .service import (
    drain_video_queue,
    prepare_video_queue,
    start_video_queue,
    stop_video_queue,
)


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "media.video_queue",
            prepare=prepare_video_queue,
            recover=start_video_queue,
            stop_producer=stop_video_queue,
            drain=drain_video_queue,
        ),
    )
