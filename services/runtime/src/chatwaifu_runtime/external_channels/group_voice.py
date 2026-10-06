"""Conservative current-command gate for opt-in group voice, never private routing."""

from __future__ import annotations

import re

_QUOTED = re.compile(
    r"```[\s\S]*?```|`[^`]*`|“[^”]*”|\u2018[^\u2019]*\u2019|「[^」]*」|\"[^\"]*\"|'[^'\n]*'"
)
_NOT_A_REQUEST = re.compile(
    r"(?:不要|别|不用|不必|不想|(?<!能)不能|(?<!可)不可|禁止|停止).{0,16}"
    r"(?:语音|声音|音频|朗读|读出来)"
    r"|(?:为什么|为何|怎么|如何).{0,16}(?:语音|声音|音频|朗读)"
    r"|(?:上次|之前|以前|曾经).{0,20}(?:让|要求|叫).{0,16}(?:语音|声音|音频|朗读)"
    r"|(?:他说|她说|他们说|有人说|他让我|她让我|转述).{0,40}(?:语音|声音|音频|朗读)"
    r"|(?:语音|声音|音频|朗读).{0,8}(?:是什么|是什么意思|含义|指什么)"
    r"|\b(?:do\s+not|don't|never|stop)\b.{0,30}\b(?:voice|audio|aloud)\b",
    re.IGNORECASE,
)
_DIRECT_REQUEST = re.compile(
    r"(?:发|发送|来|说).{0,12}(?:语音|音频)"
    r"|(?:用|以|通过).{0,12}(?:语音|声音|音频).{0,8}(?:说|讲|回|答|聊|读)"
    r"|(?:语音|音频)(?:回|说|回答|回复)"
    r"|(?:读|念).{0,20}(?:出来|出声)|(?:读|念)给我听"
    r"|(?:请|帮我|给我|把|来).{0,12}朗读"
    r"|^朗读(?:一下|这|那|以下|下面|上面|刚才|上一|一遍|[:\s])"
    r"|\b(?:send|give|use|reply)\b.{0,35}\b(?:voice|audio)\b"
    r"|\bread\b.{0,30}\baloud\b",
    re.IGNORECASE,
)


def requests_group_voice(text: str) -> bool:
    """Only an unquoted direct request can open the group reply tool.

    Ambiguous phrasing fails closed to text. This bounded recognition never
    reads history/listening evidence, does not call a model, and does not force
    a model to send voice. Owner-private semantic selection remains unchanged.
    """
    if not text.strip() or len(text) > 20_000:
        return False
    current = " ".join(_QUOTED.sub(" ", text).split())
    return not _NOT_A_REQUEST.search(current) and bool(_DIRECT_REQUEST.search(current))
