"""Reuse the frozen messaging1 runner for a bounded, fixed-history comparison.

Each baseline/candidate pair receives the same current question and history.
These synthetic fixtures reproduce the reported reply shape without account IDs.
No Runtime is started, no live memory is read, and no channel message is sent.
"""

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("frozen_messaging_runner", Path(args.runner))
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)

    greeting_history = (
        ("user", "你好"),
        ("assistant", "你好……"),
    )
    verbose_history = (
        *greeting_history,
        ("user", "你好"),
        ("assistant", "你好……\n\n真是的，你到底打算跟我说多少遍“你好”呀？"),
    )
    runner.CASES = (
        ("greeting", "你好", "answer", "gentle", ()),
        ("repeated_greeting", "你好", "answer", "gentle", greeting_history),
        ("brief_followup", "不知道", "answer", "gentle", verbose_history),
        (
            "playful_followup",
            "一直说",
            "answer",
            "gentle",
            (*verbose_history, ("user", "不知道"), ("assistant", "不知道要说什么吗……")),
        ),
        (
            "explicit_two_sentences",
            "今天心情有点差，别给建议，也别问原因，陪我聊两句就好。",
            "comfort",
            "gentle",
            (),
        ),
        (
            "detailed_code",
            "详细写一个 Python asyncio 例子：主任务用 TaskGroup 同时运行两个子任务，"  # noqa: RUF001
            "子任务 A 抛 ValueError，子任务 B 等待时会被取消。展示可运行完整代码，"
            "捕获异常组；解释取消和资源清理为什么放在 finally，不能吞 CancelledError。",  # noqa: RUF001
            "answer",
            "serious",
            (),
        ),
    )
    asyncio.run(runner.main(args))


if __name__ == "__main__":
    main()
