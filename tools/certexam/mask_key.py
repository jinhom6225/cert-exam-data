"""Register the service key and its URL-encoded forms as masked values.

GitHub masks the secret as stored, but not `quote(key)` forms that could
appear if a URL were ever printed. Run only inside GitHub Actions, in the
same step that has DATA_GO_KR_SERVICE_KEY in `env`.
"""

import os
import sys
import urllib.parse
from typing import Mapping, TextIO

from .constants import KEY_ENV


def main(env: Mapping[str, str], out: TextIO) -> int:
    if env.get("GITHUB_ACTIONS") != "true":
        print("mask_key: only for GitHub Actions", file=out)
        return 1
    key = (env.get(KEY_ENV) or "").strip()
    if not key:
        print(f"mask_key: {KEY_ENV} is not set", file=out)
        return 1
    forms = {key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)}
    for form in sorted(forms):
        print(f"::add-mask::{escape_command_value(form)}", file=out)
    return 0


def escape_command_value(value: str) -> str:
    """Workflow command data escaping: % first, then CR and LF."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


if __name__ == "__main__":
    sys.exit(main(os.environ, sys.stdout))
