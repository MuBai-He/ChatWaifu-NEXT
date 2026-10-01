"""Current-user operation intent, distinct from lexical capability relevance.

This bounded language policy does not choose capability names or parse model
prose. Unrecognized requests retain native automatic tool selection. Explicit
operations and externally verifiable questions require a recorded tool result.
"""

from __future__ import annotations

import re

_CLAUSE_START = r"(?:^|[，,。.!?！？\uFF1B;\n]|(?:并|然后|顺便))\s*"
_POLITE = r"(?:(?:你)?(?:请(?:你)?|帮我|替我|给我|麻烦(?:你)?|能不能|可以(?:帮我)?|重新|再)\s*)*"
_OPERATION = re.compile(
    _CLAUSE_START
    + _POLITE
    + r"(?:调用|执行|读取|查看|查询|查一下|查下|搜索|搜一下|联网|浏览|核查|核实|查证|"
    r"看下|看一下|看看|找一下|创建|新建|修改|更新|添加|删除|移除|取消|设置|保存|"
    r"发送|打开|关闭|开启|播放|上传|下载|安装|卸载|移动|重命名|"
    r"(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)*"
    r"(?:call|invoke|execute|read|check|look\s+up|search|browse|verify|create|add|"
    r"update|modify|delete|remove|cancel|set|save|send|open|close|play|upload|"
    r"download|install|uninstall|move|rename)\b)",
    re.IGNORECASE,
)
_REMINDER = re.compile(r"提醒我|叫醒我|\b(?:remind|wake)\s+me\b", re.IGNORECASE)
_NO_CONVERSATIONAL_FOLLOWUP = re.compile(
    r"^(?:(?:不(?:用|必|要)|别)(?:再|继续)?(?:提醒我|追问我|跟进)(?:了|啦)?|"
    r"no (?:further|more) (?:reminders|follow[- ]ups))$",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://", re.IGNORECASE)
_QUESTION = re.compile(
    r"[?？]|哪些|有什么|多少|几点|几号|是否|\b(?:what|which|when|how|is|are)\b",
    re.IGNORECASE,
)
_EXTERNAL_FACT = re.compile(
    r"法规|规定|政策|航班|天气|预报|汇率|价格|股价|运行状态|运行情况|"
    r"(?:我的|今天的|明天的)(?:日历|日程|待办|提醒|笔记|文件)|"
    r"\b(?:regulations?|polic(?:y|ies)|flights?|weather|forecast|exchange\s+rate|"
    r"stock\s+price|my\s+(?:calendar|agenda|tasks?|reminders?|notes?|files?))\b",
    re.IGNORECASE,
)
_CLOCK_QUESTION = re.compile(
    r"(?:现在|今天|当前).{0,12}(?:几点|几号|日期|星期几|什么日子)|"
    r"\b(?:what\s+(?:time|date|day)\s+is\s+it|current\s+(?:time|date))\b",
    re.IGNORECASE,
)
_PERSONAL_DATA_REQUEST = re.compile(
    _CLAUSE_START
    + _POLITE
    + r"(?:列出|显示)(?:一下)?我的|"
    + _CLAUSE_START
    + r"(?:please\s+|can\s+you\s+|could\s+you\s+)*(?:list|show)\s+my\b",
    re.IGNORECASE,
)


def requires_external_operation(user_text: str) -> bool:
    """Recognize explicit operation requests without treating mentions as actions.

    An objectless request to stop conversational follow-ups does not cancel saved
    reminders. A dated or named reminder request is left intact. Only the current
    user text participates; old dialogue, memories and source bodies cannot demand
    an operation. Native auto still handles wording outside this bounded policy.
    """
    clauses = re.split(r"[，,。.!?！？\uFF1B;\n]+", user_text)
    operation_text = "。".join(
        clause for clause in clauses if not _NO_CONVERSATIONAL_FOLLOWUP.fullmatch(clause.strip())
    )
    return bool(
        _URL.search(user_text)
        or _OPERATION.search(operation_text)
        or _REMINDER.search(operation_text)
        or _CLOCK_QUESTION.search(operation_text)
        or _PERSONAL_DATA_REQUEST.search(operation_text)
        or (_EXTERNAL_FACT.search(operation_text) and _QUESTION.search(user_text))
    )
