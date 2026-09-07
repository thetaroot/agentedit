from services.brain import BrainService


class Gateway:
    def __init__(self) -> None:
        self._brain = BrainService()

    def run(self, msg: str) -> str:
        return self._brain.handle_mcp(msg)

    def missing(self) -> None:
        self._never_defined.call()


def local_dispatch() -> str:
    srv = BrainService()
    return srv.handle_mcp("x")
