"""Build jm_list.json from a saved 15003024 getList response (plan D25).

Usage: python3 -m tools.certexam.build_jm_list <getList.xml> <out.json>
The same file is bundled in the app as assets/data/jm_list.json.
"""

import json
import sys
from pathlib import Path
from typing import List, Sequence

from .errors import CollectorError
from .parse_lists import JmEntry, parse_jm_list
from .payload import decode


def render_jm_list(entries: List[JmEntry]) -> bytes:
    lines = ",\n".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for entry in sorted(entries, key=lambda e: str(e["jmCd"]))
    )
    return ("[\n" + lines + "\n]\n").encode("utf-8")


def build(source: Path) -> bytes:
    return render_jm_list(parse_jm_list(decode("getList", source.read_bytes()).rows))


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        data = build(Path(argv[0]))
    except (OSError, CollectorError) as error:
        print(f"build_jm_list failed ({type(error).__name__})", file=sys.stderr)
        return 1
    Path(argv[1]).write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
