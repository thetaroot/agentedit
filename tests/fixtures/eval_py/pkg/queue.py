from pkg.types import Job


class Queue:
    def __init__(self) -> None:
        self._jobs: list[Job] = []

    def push(self, job: Job) -> None:
        self._jobs.append(job)

    def pop(self) -> Job | None:
        if not self._jobs:
            return None
        return self._jobs.pop(0)


def drain_queue(queue: Queue) -> list[Job]:
    out: list[Job] = []
    while True:
        job = queue.pop()
        if job is None:
            break
        out.append(job)
    return out
