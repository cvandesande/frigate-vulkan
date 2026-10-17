#!/usr/bin/env python3
"""Add an exact-match `/anon` location proxying the MoQ relay's WebSocket
door on 127.0.0.1:8557, so the browser live player's `wss://<host>/anon`
dial reaches the relay instead of answering with the web page. `location =`
sends only `/anon` there and leaves every other path on the donor's
locations; `auth_request.conf` runs first so the relay sees only signed-in
users. No EXPECTED_MD5: this script runs after every other nginx patch in
the Dockerfile, and the script before it already pins the state it sees
with its own guard. The optional path argument patches a fixture.
"""

import sys
from pathlib import Path

DEFAULT_TARGET = Path("/usr/local/nginx/conf/nginx.conf")

# Server-level anchor: the donor's image-regex location, the same line
# patch_corvette_api_location.py's NEW ends with; the new block goes
# directly before it.
OLD = """        location ~* /api/.*\\.(jpg|jpeg|png|webp|gif)$ {
"""

NEW = """        location = /anon {
            include auth_request.conf;
            proxy_pass http://127.0.0.1:8557;
            include proxy.conf;
        }
        location ~* /api/.*\\.(jpg|jpeg|png|webp|gif)$ {
"""


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    source = target.read_text(encoding="utf-8")

    if NEW in source:
        print("patch_anon_location: already applied")
        return 0

    count = source.count(OLD)
    if count != 1:
        print(
            f"patch_anon_location: the image-regex location line this patch "
            f"anchors on occurred {count} times in {target}, expected exactly "
            "once -- refusing to write",
            file=sys.stderr,
        )
        return 1

    assert "include auth_request.conf;" in NEW, (
        "patch_anon_location: the block being inserted does not include "
        "auth_request.conf; -- refusing to expose the relay without the "
        "sign-in check"
    )

    target.write_text(source.replace(OLD, NEW), encoding="utf-8")
    print("patch_anon_location: /anon location inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
