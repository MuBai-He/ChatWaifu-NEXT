"""Stable application errors; adapters and transport layers share these identities."""


class KnowledgeAccessError(ValueError):
    pass


class KnowledgeConflict(ValueError):
    pass
