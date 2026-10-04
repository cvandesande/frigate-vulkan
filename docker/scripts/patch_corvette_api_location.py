#!/usr/bin/env python3
"""Add a `/corvette/api/` nginx location for the read-only Corvette API
service (Corvette issue #4, D8).

`^~` keeps the donor's `/api/` image regex off `/corvette/api/...jpg`;
proxy.conf is left out because the service speaks only HTTP/1.0; no
caching, because responses are per user and the service already sends
`Cache-Control: no-store`. EXPECTED_MD5 is nginx.conf's md5 with every
earlier patch applied, read from a real build, not computed by hand -- a
running container's copy differs by one line because Frigate's own start
rewrites `worker_processes`. The optional path argument patches a fixture.
"""

import hashlib
import sys
from pathlib import Path

DEFAULT_TARGET = Path("/usr/local/nginx/conf/nginx.conf")

# md5 of nginx.conf inside a real build of this branch with every earlier
# nginx patch applied (2026-10-04); a running container's copy differs.
EXPECTED_MD5 = "e38de57b35f8e2e63bd8c082588ef6dd"

# Server-level anchor: the donor's image regex location, immediately before
# which the new block goes so `^~` outranks it for /corvette/api/ paths.
OLD = """        location ~* /api/.*\\.(jpg|jpeg|png|webp|gif)$ {
"""

NEW = """        location ^~ /corvette/api/ {
            include auth_request.conf;
            limit_except GET {
                deny  all;
            }
            proxy_http_version 1.0;
            proxy_pass_request_body off;
            proxy_pass http://unix:/run/corvette-api/api.sock:;
        }
        location ~* /api/.*\\.(jpg|jpeg|png|webp|gif)$ {
"""


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    raw = target.read_bytes()
    actual_md5 = hashlib.md5(raw).hexdigest()

    source = raw.decode("utf-8")
    if NEW in source:
        print("patch_corvette_api_location: already applied")
        return 0

    if actual_md5 != EXPECTED_MD5:
        print(
            f"patch_corvette_api_location: {target} is md5 {actual_md5}, but the "
            f"expected nginx.conf is {EXPECTED_MD5} -- either FRIGATE_IMAGE has "
            "drifted, or an earlier nginx patch script changed. Re-verify the "
            "insertion point by hand and update EXPECTED_MD5 here, then retry.",
            file=sys.stderr,
        )
        return 1

    if OLD not in source:
        # Unreachable if the md5 check above passed; kept as a named failure
        # rather than a silent no-op, matching the other scripts' precedent.
        print(
            "patch_corvette_api_location: nginx.conf's md5 matched but the "
            "/api/ image-regex location this patch anchors on was not found "
            "verbatim -- the file and the recorded md5 disagree with each other",
            file=sys.stderr,
        )
        return 1

    target.write_text(source.replace(OLD, NEW), encoding="utf-8")
    print("patch_corvette_api_location: /corvette/api/ location inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
