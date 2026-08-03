#!/usr/bin/env python3
"""Make frigate.util.image.UntrackedSharedMemory correct on Python 3.13.

UntrackedSharedMemory works around cpython#82300 by monkeypatching
resource_tracker.register to a no-op while SharedMemory.__init__ runs, so the
segment is never registered. That issue was fixed in 3.13, which added a native
``track`` keyword -- and the fix breaks the workaround: super().__init__()
now accepts track=True by default and assigns self._track = True, overwriting
the self._track = track the subclass set moments earlier. Registration is still
suppressed, so unlink() then calls resource_tracker.unregister() on a name that
was never registered and the tracker process raises

    KeyError: '/out-test'

on every shutdown, once per camera. Passing track=False through to super()
expresses the same intent using the mechanism 3.13 provides, and drops the
monkeypatch entirely.

Fails loudly if the upstream source no longer matches, so a Frigate bump cannot
silently leave this unpatched.
"""

import sys
from pathlib import Path

TARGET = Path("/opt/frigate/frigate/util/image.py")

OLD = """        # lock so that other threads don't attempt to use the
        # register function during this time
        with self.__lock:
            # temporarily disable registration during initialization
            orig_register = _mprt.register
            _mprt.register = self.__tmp_register

            # initialize; ensure original register function is
            # re-instated
            try:
                super().__init__(name=name, create=create, size=size)
            finally:
                _mprt.register = orig_register
"""

NEW = """        # Python 3.13 fixed cpython#82300 by adding a native track keyword,
        # which makes the register() monkeypatch below unnecessary and wrong:
        # super().__init__() would reset self._track to True, so unlink() would
        # unregister a name that was never registered. Patched in by
        # docker/scripts/patch_untracked_shm.py.
        return super().__init__(name=name, create=create, size=size, track=False)
"""


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if NEW in source:
        print("patch_untracked_shm: already applied")
        return 0
    if OLD not in source:
        print(
            "patch_untracked_shm: UntrackedSharedMemory.__init__ no longer matches "
            "the expected source; re-check the workaround against this Frigate "
            "version before shipping.",
            file=sys.stderr,
        )
        return 1
    TARGET.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print("patch_untracked_shm: applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
