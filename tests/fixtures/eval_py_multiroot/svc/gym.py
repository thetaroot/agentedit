"""Mirror of the E2E lazy-import pattern (moltbot-core permission manager)."""


def build_brain():
    from services.brain import BrainBuilder

    return BrainBuilder.build()
