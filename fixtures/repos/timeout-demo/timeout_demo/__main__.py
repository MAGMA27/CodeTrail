"""Command-line entry point for the timeout demo."""

from __future__ import annotations

import json
import os

from .app import start


def main() -> None:
    """Run the deterministic local demo."""

    print(json.dumps(start(os.environ), sort_keys=True))


if __name__ == "__main__":
    main()
