"""Provider app (like moltbot-core): this dir is the import root at runtime."""


class BrainService:
    def compute(self, x: int) -> int:
        return x * 2


def standalone(v: int) -> int:
    return v + 1


class BrainBuilder:
    @classmethod
    def build(cls) -> "BrainBuilder":
        return cls()
