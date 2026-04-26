__all__ = ["AgentService"]


def __getattr__(name: str):
    if name == "AgentService":
        from .service import AgentService

        return AgentService
    raise AttributeError(name)
