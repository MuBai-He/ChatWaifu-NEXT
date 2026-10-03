"""Conservative fresh user request gate for current-turn voice delivery."""

import re

_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`]*`')
_NEGATIVE = re.compile(
    r"(?:不要|不用|别|禁止|取消|停止).{0,12}(?:语音|读|念)"
    r"|(?:no|don't|do not|stop).{0,15}(?:voice|aloud)",
    re.I,
)
_VOICE = re.compile(
    r"^(?:(?:我希望|我想要?|请|你|能不能|可以|能|帮我|给我|麻烦|现在|这次|这回|接下来)[，,\s]*)*"
    r"(?:用语音|通过语音|(?:发|来)(?:给我)?(?:一条|一段|条|段|个)?语音|"
    r"(?:改成|换成)语音|语音(?:回复|回答|说)|(?:把|将).{0,80}(?:读|念)给我听|(?:读|念)给我听)"
    r"|^(?:please\s+)?(?:send\s+(?:me\s+)?(?:a\s+)?voice|"
    r"(?:reply|respond|say).{0,20}(?:by|with|in)\s+voice|read.{0,80}aloud)",
    re.I,
)
_META = re.compile(
    r"^(?:解释|介绍|讨论|分析|什么|如何|怎么|为什么|他说|她说|他问|她问)|是什么意思|怎么实现"
)


def requests_voice(text: str) -> bool:
    request = _QUOTED.sub("", text).strip()
    if _NEGATIVE.search(request):
        return False
    # A leading explicit output instruction remains valid for technical content.
    if not _VOICE.search(request):
        return False
    if request.startswith(("发语音", "语音")) and _META.search(request):
        return False
    return True
