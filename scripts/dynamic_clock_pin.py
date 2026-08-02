#!/usr/bin/env python3
"""Pin the GPU clock only while Frigate is actually detecting.

Vega 20's SMU does not ramp this card up under ncnn inference: it sits at
808 MHz with gpu_busy_percent around 22%, so every live inference pays the
downclocked rate and frames are dropped when both cameras have motion at once.
Forcing power_dpm_force_performance_level=high fixes that but costs ~15-19 W
continuously, most of it wasted overnight when nothing is being detected.

This holds "high" only while detection load is up, and releases to "auto" after
a quiet dwell. See docs/vulkan-notes.md for the measurements behind the
defaults.

Root is required to write the performance level. The level is restored to auto
on exit, including on SIGTERM.
"""

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEVICE = Path("/sys/class/drm/card1/device")
PERF = DEVICE / "power_dpm_force_performance_level"
HWMON = DEVICE / "hwmon/hwmon5"


def read_int(path):
    """sysfs on this card returns EBUSY while the GPU is runtime-suspended,
    which is normal at idle rather than an error."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def current_sclk():
    try:
        for line in (DEVICE / "pp_dpm_sclk").read_text().splitlines():
            if line.rstrip().endswith("*"):
                return int(line.split(":")[1].strip().split("Mhz")[0])
    except (OSError, ValueError, IndexError):
        pass
    return None


def frigate_stats(url, timeout):
    """Returns (detection_fps, skipped_fps, inference_ms) or None if Frigate is
    unreachable. A failed poll must never take the daemon down: Frigate
    restarts on its own schedule and the level is safe to leave as-is."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            stats = json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None
    cameras = stats.get("cameras", {})
    detectors = stats.get("detectors", {})
    detection = sum(c.get("detection_fps", 0) or 0 for c in cameras.values())
    skipped = sum(c.get("skipped_fps", 0) or 0 for c in cameras.values())
    inference = next(
        (d.get("inference_speed") for d in detectors.values() if d.get("inference_speed")),
        None,
    )
    return detection, skipped, inference


class Governor:
    def __init__(self, args):
        self.args = args
        self.level = None
        self.quiet_polls = 0
        self.pinned_since = None
        self.pinned_seconds = 0.0
        self.transitions = 0

    def set_level(self, level, reason):
        if level == self.level:
            return
        try:
            PERF.write_text(level + "\n")
        except OSError as error:
            print(f"! could not set {level}: {error}", flush=True)
            return
        now = time.time()
        if level == "high":
            self.pinned_since = now
        elif self.pinned_since is not None:
            self.pinned_seconds += now - self.pinned_since
            self.pinned_since = None
        self.level = level
        self.transitions += 1
        print(f"{time.strftime('%H:%M:%S')} -> {level:4s}  ({reason})", flush=True)

    def duty(self, started):
        pinned = self.pinned_seconds
        if self.pinned_since is not None:
            pinned += time.time() - self.pinned_since
        elapsed = max(1e-9, time.time() - started)
        return 100.0 * pinned / elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:5000/api/stats")
    parser.add_argument("--poll", type=float, default=5.0)
    # Pin well below the load where skipping starts (~17-20 det/s measured), so
    # the clock is already up by the time a burst reaches its peak.
    parser.add_argument("--pin-above", type=float, default=6.0)
    parser.add_argument("--release-below", type=float, default=3.0)
    # Dwell before releasing, in polls. Motion arrives in bursts with short
    # gaps; releasing into a gap would drop the clock right before the next one.
    parser.add_argument("--release-polls", type=int, default=12)
    parser.add_argument("--telemetry-every", type=int, default=2)
    args = parser.parse_args()

    governor = Governor(args)

    def shutdown(signum, frame):
        governor.set_level("auto", f"signal {signum}")
        print(f"pinned duty cycle: {governor.duty(started):.1f}%  "
              f"transitions: {governor.transitions}", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    started = time.time()
    governor.set_level("auto", "startup")
    print("time state det_fps skipped inference_ms sclk power_w", flush=True)

    poll_index = 0
    while True:
        poll_index += 1
        stats = frigate_stats(args.url, timeout=args.poll / 2)
        if stats is None:
            # Hold the current level rather than guessing. Frigate being down
            # is not a reason to change the card's power state.
            print(f"{time.strftime('%H:%M:%S')} ! frigate stats unavailable", flush=True)
            time.sleep(args.poll)
            continue

        detection, skipped, inference = stats
        # Skipping is the symptom the pinning exists to prevent, so treat any
        # of it as a demand signal in its own right, not only high detection_fps.
        if detection >= args.pin_above or skipped > 0:
            governor.quiet_polls = 0
            governor.set_level("high", f"det_fps={detection:.1f} skipped={skipped:.1f}")
        elif detection < args.release_below:
            governor.quiet_polls += 1
            if governor.quiet_polls >= args.release_polls:
                governor.set_level("auto", f"quiet for {governor.quiet_polls} polls")
        else:
            governor.quiet_polls = 0

        if poll_index % args.telemetry_every == 0:
            power = read_int(HWMON / "power1_input")
            print(
                f"{time.strftime('%H:%M:%S')} {governor.level:4s} "
                f"{detection:5.1f} {skipped:4.1f} "
                f"{inference if inference is not None else float('nan'):6.1f} "
                f"{current_sclk() or -1:5d} "
                f"{(power / 1e6) if power else float('nan'):6.1f} "
                f"duty={governor.duty(started):.0f}%",
                flush=True,
            )
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
