#!/usr/bin/env python3
"""Add a `/corvette/api/v1/media/` nginx location serving recorded-range
playlists through the vod module (Corvette issue #5).

The block goes at server level before the `/corvette/api/` block; its
longer `^~` prefix claims these paths. EXPECTED_MD5 is nginx.conf's md5
with every earlier patch applied, from a fixture chain run of the
vendored donor file (2026-11-03). The optional path argument patches a
fixture.
"""

import hashlib
import sys
from pathlib import Path

DEFAULT_TARGET = Path("/usr/local/nginx/conf/nginx.conf")

# md5 of nginx.conf with every earlier nginx patch applied, from a fixture
# chain run of the vendored donor file (2026-11-03); built image UNVERIFIED.
EXPECTED_MD5 = "894fe25f3aa82974d7708956b621a161"

# Server-level anchor: the `/corvette/api/` block opening; carrying the
# auth include too keeps this two-line anchor unique.
OLD = """        location ^~ /corvette/api/ {
            include auth_request.conf;
"""

NEW = """        location ^~ /corvette/api/v1/media/ {
            include auth_request.conf;
            limit_except GET {
                deny  all;
            }
            vod hls;
            vod_hls_container_format fmp4;
            vod_upstream_location /corvette-vod-lookup;
            add_header Cache-Control "no-store";
            expires off;
            error_page 502 =404 /corvette-vod-not-found;
        }
        location = /corvette-vod-not-found {
            default_type application/json;
            return 404 '{"code": "not_found", "message": "no recording for this range"}';
        }
        # UNVERIFIED: this rewrite assumes the module's mapping request
        # arrives as /corvette-vod-lookup/corvette/api/v1/media/<path>;
        # the human-run deploy probe after the build settles the URI shape.
        location ^~ /corvette-vod-lookup/ {
            internal;
            rewrite ^/corvette-vod-lookup/corvette/api/v1/media/(.*)$ /api/vod/$1 last;
        }
        location ^~ /corvette/api/ {
            include auth_request.conf;
"""


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    raw = target.read_bytes()
    actual_md5 = hashlib.md5(raw).hexdigest()

    source = raw.decode("utf-8")
    if NEW in source:
        print("patch_corvette_media_location: already applied")
        return 0

    if actual_md5 != EXPECTED_MD5:
        print(
            f"patch_corvette_media_location: {target} is md5 {actual_md5}, but the "
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
            "patch_corvette_media_location: nginx.conf's md5 matched but the "
            "/corvette/api/ location this patch anchors on was not found "
            "verbatim -- the file and the recorded md5 disagree with each other",
            file=sys.stderr,
        )
        return 1

    target.write_text(source.replace(OLD, NEW), encoding="utf-8")
    print("patch_corvette_media_location: /corvette/api/v1/media/ location inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
