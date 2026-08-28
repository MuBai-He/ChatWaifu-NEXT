"""Install the small language resource Pipecat needs for sentence boundaries."""

from nltk_resources import DEFAULT_NLTK_DATA_ROOT, ensure_punkt_tab


def main() -> int:
    resource = ensure_punkt_tab(DEFAULT_NLTK_DATA_ROOT)
    print(f"NLTK punkt_tab is ready at {resource}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
