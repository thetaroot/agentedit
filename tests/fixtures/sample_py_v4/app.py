from brain import BrainService


class _Router:
    def post(self, path: str):
        def deco(fn):
            return fn
        return deco


router = _Router()


@router.post("/items")
def create_item() -> None:
    holder = Holder(service)
    holder.go()


class Base:
    def base_run(self) -> int:
        return 1


class Sub(Base):
    def work(self) -> int:
        return super().base_run() + self.base_run() + self.go()

    def go(self) -> int:
        return 1


class Holder:
    def __init__(self, brain: BrainService):
        self._brain = brain
        self._untyped = None

    def go(self) -> int:
        self._brain.run()
        return self._helper()

    def _helper(self) -> int:
        return self.more()

    def more(self) -> int:
        return 1

    def probe_untyped(self) -> None:
        # `_untyped` has no type annotation and is assigned None: the call to
        # persist_unique stays unresolved (dynamic) and must be surfaced as a
        # suspected reference on the target method, never fabricated as an edge.
        if self._untyped:
            self._untyped.persist_unique({"a": 1})


service = BrainService()
