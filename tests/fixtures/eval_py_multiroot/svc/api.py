from services.brain import BrainService, standalone


def make() -> BrainService:
    return BrainService()


def use(b: BrainService) -> int:
    return b.compute(1) + standalone(2)
