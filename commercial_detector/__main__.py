"""Entry point for: python -m commercial_detector"""

import sys

from commercial_detector.main import run


def main():
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
