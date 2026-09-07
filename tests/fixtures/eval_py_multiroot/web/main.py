"""Second app; at runtime it puts the svc/ dir on sys.path."""
from services.brain import BrainService


def run() -> int:
    b = BrainService()
    return b.compute(3)
