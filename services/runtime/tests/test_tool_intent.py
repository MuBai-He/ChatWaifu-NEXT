"""Operation necessity is distinct from capability mentions and dialogue."""

import pytest
from chatwaifu_runtime.agent.tool_intent import requires_external_operation


@pytest.mark.parametrize(
    "text",
    [
        "听说宁宁很容易害羞，是不是真的呀？",
        "时间不早了，今天就先聊到这里吧。",
        "好的，谢谢，旅行的事到这里就结束了。晚安，不用再提醒我。",
        "就这样吧，晚安，别再提醒我了。",
        "Thanks, that's all. Good night. No further reminders.",
        "别再开这个玩笑了，我想认真聊聊。",
        "提醒这个功能真方便。",
        "什么是提醒？",
        "帮我解释取消提醒是什么意思。",
        "我应该怎么创建提醒？",
        "What does cancel reminder mean?",
        "How do I create a reminder?",
        "请写一首短诗。",
        "Please write a short poem.",
        "你背后是用什么系统运行的？本地还是云端？",
        "今天外面的风挺大的，吹得窗户呼呼响。",
        "关于 Raft 的选举超时，请解释一下。",
        "不要打开 https://example.org/guide，只解释这个 URL 的组成。",
        "Don't open https://example.org/guide; explain its URL structure only.",
        "Create a short poem about rain.",
        "请创建一首关于雨的短诗。",
        "Please check this calculation: 17 * 23 = 391.",
        '请看看这段代码有没有语法错误\uff1aprint("hello")',
    ],
)
def test_dialogue_explanation_and_closure_do_not_require_an_operation(text: str) -> None:
    assert not requires_external_operation(text)


@pytest.mark.parametrize(
    "text",
    [
        "请读取 https://example.org/source 。",
        "看看运行状态。",
        "请调用刚安装的工具。",
        "请核实今天的最新规定。",
        "乘坐国内航班时有哪些具体携带规定？",
        "今天的天气怎么样？",
        "现在几点？",
        "What time is it?",
        "晚安，取消明天上午的提醒。",
        "晚安，不用再提醒我明天上午的会议。",
        "晚安，明天八点提醒我出发。",
        "把刚才的公告整理成清单，并重新查询今天的最新规定。",
        "把刚才的公告整理成清单，并提醒我明天上午检查。",
        "明天午饭后提醒我检查充电宝。",
        "查一下我的日程。",
        "请列出我的待办。",
        "显示我的日历。",
        "Please show my calendar.",
        "Can you cancel my reminder for tomorrow?",
        "Remind me tomorrow at nine.",
        "请修改我的待办。",
        "请发送这条消息。",
        "请删除这个文件。",
        "Create a short poem about rain and save it to my files.",
        "Create a short poem about rain, then send it to Alex.",
        "请创建一首短诗，并保存到文件。",
        "Please check this calculation: 17 * 23 = 391; also check my calendar.",
        '请看看这段代码有没有语法错误\uff1aprint("hello")，然后读取网页。',
        "不要打开 https://example.org/guide，然后读取 https://example.org/other 。",
        "Please check my calendar.",
        "请核实今天的最新规定。",
    ],
)
def test_explicit_operation_or_external_fact_request_requires_results(text: str) -> None:
    assert requires_external_operation(text)
