"""An index into the existing task journal, never a second execution state machine."""

from uuid import UUID


class ActionAlreadyClaimed(Exception):
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id
        super().__init__("Action already belongs to a durable task")
