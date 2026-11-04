"""Conservative relevance of unconfirmed work, separate from completion or authority."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

TemporalStatus = Literal["current", "historical", "uncertain"]
RECENT_EVIDENCE_DAYS = 7
TEMPORAL_PROMPT = (
    "\ncurrent_local_time 是可信的本次整理时间，仅用于判断现在是否还需要处理;"
    "消息中的相对日期始终以各自 message_local_time/message_time_ms 为准。"
    "历史上有人提出任务，不等于现在仍未完成。已过去的日程、旧的临时请求与"
    "没有近期证据的历史承诺，保留当时事实及不确定性，不宣称现在仍待执行。"
    "未确认且截止时间已过的待办移至历史核对。明确未来安排可以保留;已确认的逾期待办不能被推断为完成或取消。"
)


def assess_work_relevance(
    kind: str,
    operation: str,
    source_times: Iterable[int],
    timezone: str,
    now: datetime,
    *,
    due_at: datetime | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    due_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[TemporalStatus, str]:
    """Assess cited fresh evidence only. Never change a work item's business state.

    Seven days matches the native recent-intake window. It is a review boundary,
    not a retention limit or proof of completion. Future arrangements survive it.
    """
    if kind not in {"todo", "event"}:
        return "current", ""
    latest = max(source_times, default=0)
    if latest <= 0 or latest > int((now + timedelta(minutes=5)).timestamp() * 1000):
        return "uncertain", "原文时间缺失或异常，需要核对是否仍需处理。"
    recent = latest >= int((now - timedelta(days=RECENT_EVIDENCE_DAYS)).timestamp() * 1000)
    if recent and operation in {"complete", "cancel"}:
        return "current", ""
    today = now.astimezone(ZoneInfo(timezone)).date()
    if kind == "event":
        # End dates are exclusive; a missing clock end does not invent a duration.
        past = (
            (end_date is not None and end_date <= today)
            or (ends_at is not None and ends_at <= now)
            or (end_date is None and start_date is not None and start_date < today)
            or (
                ends_at is None
                and starts_at is not None
                and starts_at.astimezone(ZoneInfo(timezone)).date() < today
            )
        )
        if past:
            return "historical", "日程时间已过去，保留历史记录，不推断现在仍需执行。"
    if kind == "todo" and (
        (due_at is not None and due_at <= now) or (due_date is not None and due_date < today)
    ):
        return "historical", "未确认待办已超过截止时间，移至历史核对是否仍需处理。"
    if recent:
        return "current", ""
    future = (
        any(value is not None and value >= now for value in (due_at, starts_at, ends_at))
        or (due_date is not None and due_date >= today)
        or (start_date is not None and start_date >= today)
        or (end_date is not None and end_date > today)
    )
    if future:
        return "current", ""
    if (due_at is not None and due_at < now) or (due_date is not None and due_date < today):
        return "historical", "旧消息中的截止时间已过去，是否仍未完成需要重新核对。"
    return "uncertain", "原文超过近期七天窗口，且没有明确未来安排;是否仍需处理待核对。"
