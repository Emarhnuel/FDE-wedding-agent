from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContext:
    company_id: str
