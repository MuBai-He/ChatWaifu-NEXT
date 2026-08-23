"""Run the exact pnpm version pinned by this repository."""

from __future__ import annotations

import os
import subprocess
import sys

from pnpm_tool import PnpmToolError, environment_with_pnpm, resolve_pnpm


def main() -> int:
    try:
        pnpm = resolve_pnpm()
    except PnpmToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    arguments = [str(pnpm), *sys.argv[1:]]
    environment = environment_with_pnpm(pnpm)
    if os.name == "posix":
        os.execve(str(pnpm), arguments, environment)
    try:
        return subprocess.run(arguments, check=False, env=environment).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
