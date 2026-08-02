# Vulkan validation notes

## Packaging

### 2026-08-02 — one GPU-neutral image for both deployments

The gfx803 and gfx906 images were being built and tagged separately while being
byte-identical in build inputs. The `frigate-vulkan` target takes only
`FRIGATE_IMAGE` and `NCNN_TAG`, both the same in every profile, and the ncnn
build carries no arch-specific flags (`-DNCNN_VULKAN=ON` only) because ncnn
compiles its Vulkan shaders at runtime after enumerating the device. RADV drives
Polaris and Vega 20 from the same Mesa build.

Everything that genuinely differs per card is runtime, and stays in the profile:
`RENDER_GID` (109 on Bookworm, 992 on Trixie) and `RADV_PERFTEST`.

Compose image names no longer interpolate `PROFILE_NAME`:

| Service | Image |
|---|---|
| `frigate-vulkan` | `…/frigate-rocm-legacy:frigate-vulkan` (override with `FRIGATE_VULKAN_IMAGE`) |
| `vulkan-smoke` | `rocm-legacy/vulkan-smoke:bookworm` |
| `vulkan-smoke-trixie` | `rocm-legacy/vulkan-smoke:trixie` |

Published by `scripts/release_image.sh` to **Docker Hub**
(`cvandesande/frigate-rocm-legacy`) under a dated immutable tag plus a moving
one; deployments reference the dated tag. Current release:
`frigate-vulkan-20260802` =
`sha256:87ca89854b19e75c0f9895d5481024ee927be197d9d2bb87607dc9bedbdb422b`.

Docker Hub rather than the GitLab registry because that repo allows anonymous
pulls, so hosts need no registry credentials — `noyan` had none and could not
pull from GitLab at all. The image was checked before publishing to a repo that
serves anonymous pulls: no secrets in its baked env, `/config` empty, no
credential strings in the layers this repo adds. Models are **not** in the image,
so nothing Frigate+ licensed is published.

CI now validates every profile rather than just gfx803, and asserts that all
profiles resolve to identical image names so per-GPU builds cannot creep back.
It also referenced `docker/scripts/install_python311.sh`, which had been replaced
by `install_python.sh` — that would have failed on the next commit.

**Deployment status: both hosts run the same digest**, `sha256:87ca8985…`, each
verified after switching (model loaded, warm-up inference logged, no extract
failures). Previously they ran separate builds of the same source.

| Host | Orchestration | GPU | Model |
|---|---|---|---|
| `noyan` | docker compose | gfx906 Vega 20 | `yolov9s-640-2026-2` |
| `tirnanog` | k8s StatefulSet | gfx803 Polaris | `yolov9t-640-20260715` |

The k8s manifest still carries `imagePullSecrets: regcred` for the old GitLab
registry. It is harmless against an anonymously-pullable Docker Hub image and
was left in place rather than removed as an untested side change.

The `gfx803-vulkan` profile builds an ncnn wheel with Vulkan enabled and uses
Mesa RADV at runtime. Standalone smoke validation has passed on the target
host; live Frigate detector inference remains under validation.

## Hardware validation

### 2026-07-19 — gfx803 Vulkan smoke test: fp32 parity passed

- GPU: AMD Radeon RX 560 Series (RADV POLARIS11), PCI `0000:08:00.0`
- Runtime: Mesa RADV `22.3.6`, Vulkan API `1.3.230`
- Smoke image: `registry.gitlab.com/cvandesande/dockers/frigate-rocm-legacy@sha256:9011b0fa6dd5e71b5655325c4584aa479f2a242242923c1f3a8fcbad79885b75`
- Model checksums: param `663dbd68837d88fb84cf9061fdeb506f51ec1ed00324d2971011a7761ff6919a`, bin `d6f06e8019a08cfb08e6e792e9962c96c94e48372b64a48ba6981cea01402c39`
- First run: mean `12.371 ms`, median `12.335 ms`, maximum error `1.2725677`
  (**failed**). ncnn's Vulkan defaults had not explicitly disabled fp16 paths.
- Corrected run (all three ncnn fp16 options explicitly `False`): mean
  `13.113 ms`, median `13.117 ms`, maximum error `0.00068664551` (**passed**;
  required `< 1e-2`). CPU and GPU repeated outputs each had zero difference.
- Worst CPU/GPU value: output `(2, 2069)`, GPU `190.33032`, CPU `190.32964`.

The temporary Pod was removed and the existing ROCm Frigate StatefulSet was
restored after each test. The smoke gate is now satisfied; Frigate integration
remains a separate pending check.

### 2026-07-19 — Frigate+ YOLOv9 base-model comparison on gfx803

All models below were downloaded from the existing Frigate+ cache, converted
from ONNX with pnnx `fp16=0`, and benchmarked on the RX 560/RADV host with
ncnn fp16 paths explicitly disabled. Every CPU/GPU parity result passed the
`< 1e-2` gate.

| Model | Resolution | Mean ms | Median ms | Max CPU/GPU error | Approx. raw FPS |
|---|---:|---:|---:|---:|---:|
| YOLOv9-t | 320 | 14.371 | 14.229 | 0.0020752 | 69.6 |
| YOLOv9-s | 320 | 22.278 | 22.261 | 0.0008850 | 44.9 |
| YOLOv9-t | 640 | 35.289 | 35.235 | 0.0023193 | 28.3 |
| YOLOv9-s | 640 | 63.571 | 63.546 | 0.0065918 | 15.7 |

For this GPU, YOLOv9-t 640 is the recommended quality/latency starting point:
it remains below 36 ms while providing higher input resolution than the 320
models. Use YOLOv9-t 320 when camera count or detection cadence needs maximum
headroom. YOLOv9-s 640 is not recommended without a camera-accuracy benefit
that justifies its 64 ms cost.

Frigate+ YOLO-NAS 320/640 base models were not benchmarked as ncnn candidates:
pnnx reported unsupported `TopK`, `Gather`, and `NonMaxSuppression` operations
when converting their ONNX graphs. They also use uint8 input and require a
YOLO-NAS-specific post-processing implementation, unlike the current
raw-YOLO ncnn plugin.

## Frigate integration

### 2026-07-19 — initial live inference segfault and corrective deployment

The initial Vulkan Frigate image loaded the latest Frigate+ YOLOv9-t 640 model
(`ea3c8aba575339b962315e9e24102e09`) with `vulkan=True`, but its first live
detector inference segfaulted in `NcnnDetector.detect_raw` at
`extractor.extract`. The same model and GPU passed standalone ncnn smoke
testing, which ruled out basic model conversion and RADV availability.

The plugin was corrected to retain the contiguous NumPy input array for the
lifetime of `ncnn.Mat` and `extractor.extract`; the previous code passed an
inline temporary array to native ncnn. The corrected image is now the running
Frigate StatefulSet image, and at 2026-07-19 22:09 local time it successfully
loaded `/config/model_cache/yolov9t-640-20260715.ncnn.param` with
`vulkan=True`. No post-fix live inference appears in the recorded logs yet, so
this is **not** evidence that the segfault is resolved.

Next: trigger/observe real detections, confirm the detector worker remains
stable, and record its inference metric. Do not promote this profile beyond
experimental until that check is complete.

Do not claim the profile is hardware-validated until both entries include the
host GPU, Mesa version, model checksum, and measurements.

### 2026-08-02 — gfx803 brought up to the current plugin; transfer_queue is a no-op on Polaris

The `icams/frigate-0` StatefulSet on `tirnanog` had been running the pre-fix
plugin: the `np.zeros((20, 6))` fallback, no `NcnnDeviceLost`, no
`_verify_device`. It was healthy — detecting normally, zero extract failures in
7d19h — but a GPU reset would have presented as `detection_fps` counting while
nothing was detected.

Rebuilt from the current Dockerfile and pushed as
`registry.gitlab.com/cvandesande/dockers/frigate-rocm-legacy:frigate-vulkan-gfx803-vulkan`
(`sha256:87ca89854b19e75c0f9895d5481024ee927be197d9d2bb87607dc9bedbdb422b`).
Rollback digest:
`sha256:c94051604a786aa671ca771bec71e9f7be458cb9ea450a3fc8c86eb41348635d`.
Post-rollout the detector logs `device verified with a warm-up inference`, and
detection resumed with no extract failures.

**The rebuild also bumped Mesa 22.3.6 → 25.0.7-2~bpo12+1**, because the
backports step was added to the shared `frigate-vulkan` target for Vega 20.
That rode along with the plugin change rather than being chosen separately.

**`RADV_PERFTEST=transfer_queue` does nothing on Polaris.** The variable is set
in the container and Mesa is 25.0.7, but ncnn still reports
`queueC=1[4] queueT=0[1]` — identical to without it. RADV exposes no SDMA
transfer queue family on Polaris 11, so unlike Vega 20 (where the flag moves the
upload to `queueT=2[2]`) there is nothing to move the weight upload onto. The
gfx-ring precondition therefore cannot be mitigated this way on this card. The
variable is left set because it is harmless and correct for other hardware.

Not investigated, deliberately: whether this card downclocks under inference the
way Vega 20 does. Prior duty-cycle numbers on it (34.7 ms sustained vs 40.4 ms
bursty) suggest its SMU ramps up correctly, which is the opposite of Vega 20,
and pinning would need a privileged DaemonSet since Talos mounts sysfs read-only
in unprivileged pods.

Camera-side tuning needed nothing: all three cameras were already at
`detect.fps: 5` with per-camera motion thresholds (50/60), timestamp masks and
object filters. The YOLOv9-s 640 recommendation from gfx906 does **not** carry
over — the gfx803 table has s-640 at 63.6 ms against t-640's 35.3 ms.

## gfx906 / Vega 20 (Radeon VII)

### 2026-08-02 — gfx ring hang under Frigate; superseded

> Kept as the record of the first pass. Its conclusions about the cause were
> wrong; see "root cause found" below. The symptom description and the model
> and host details still stand.

Host `noyan`: Debian 13 trixie, kernel 7.1.3, Mesa 25.0.7, AMD Radeon VII
(RADV VEGA20, PCI `0000:03:00.0`), Xeon E3-1230 v3. `RENDER_GID` is **992** on
trixie, not the 109 the Bookworm-based profile assumes.

Model: Frigate+ `yolov9s` 320x320 (`0a4483a45c211eae1e8ba2daebff2b37`),
converted from the cached ONNX with pnnx `20260526`, `fp16=0`, 41 classes.
Checksums — param `fc212d8046cd4f5527837d22368597b7286a757e34e5a553220969cfd5b48341`,
bin `2f8fc01b7c5bffb83ffa7c63e5b1367ee12364569ab45daea4f2d6a3a72dc4d1`.

**Symptom.** Under Frigate the detector wedges the GPU within ~15 s of starting:

```
amdgpu: ring gfx timeout, signaled seq=342261, emitted seq=342262
amdgpu:  Process frigate.detecto pid 115933
amdgpu: GPU reset begin! ... BACO reset ... VRAM is lost due to GPU reset!
```

The reset leaves that process's `VkDevice` permanently `VK_ERROR_DEVICE_LOST`,
so `vkQueueSubmit` returns `-4` and every later `extract` returns `-1` forever.
The plugin's zero-array fallback keeps Frigate alive, so the only visible
symptom is `detection_fps` counting normally while nothing is ever detected.
Seven resets were recorded during this investigation. The host stayed up each
time (console is the ASPEED BMC, not the Radeon).

**Measured, standalone — all passing.** With Frigate stopped, the same image,
model, ncnn build, and `NcnnDetector` class:

| Run | Mesa | Iterations | Result | Mean ms |
|---|---|---:|---|---:|
| smoke | 22.3.6 | 50 | pass | 13.3 |
| soak | 22.3.6 | <1000 | **GPU reset** | — |
| soak | 25.0.7 | 20000 | pass | 16.35 |
| soak + VAAPI active | 25.0.7 | 20000 | pass | 15.12 |
| real `NcnnDetector`, 3000 calls | 25.0.7 | 3000 | pass | 14.59 |

CPU/GPU parity passed throughout (max error `0.00091552734`, gate `< 1e-2`).

**Hypotheses tested and eliminated.** None of these is the cause:

- *Malformed input tensor.* Instrumented `detect_raw`: Frigate delivers
  `(1,3,320,320)` float32, squeezing to a C-contiguous `(3,320,320)`, producing
  `Mat w=320 h=320 c=3 dims=3 elemsize=4`. Byte-identical to a passing manual call.
- *Wrong blob names.* `_parse_blob_names` returns `('in0','out0')`, correct.
- *fork-safety.* A forked child works whether or not the parent initialised
  Vulkan first.
- *RLIMIT_MEMLOCK.* Identical (8 MB) in both the failing detector process and a
  passing `docker exec` process.
- *Old Mesa.* 22.3.6 does hang a standalone soak in <1000 iterations and 25.0.7
  survives 20000 — a real and reproducible difference — but **25.0.7 still hangs
  under Frigate**. The driver upgrade is necessary, not sufficient.
- *Concurrent VAAPI decode.* A 20000-iteration soak passes with nine ffmpeg
  VAAPI processes running. Removing `hwaccel_args` entirely does **not** stop
  the Frigate hang.

**Still open.** The trigger is specific to how Frigate drives the detector, not
to the model, the plugin, the driver, or GPU contention. The untested structural
difference is the `multiprocessing` **forkserver** child (preloading `sqlite3`,
`numpy`, `cv2`, `peewee`, `zmq`, `ruamel.yaml`) at `PROCESS_PRIORITY_HIGH`; that
process holds two fds on `/dev/dri/renderD128`. Next step is to reproduce a
detector inside a forkserver child with the same preloads, outside Frigate.

**Note on the existing smoke gate.** Its 50-iteration default is roughly an
order of magnitude too short to catch this class of fault: it passed cleanly at
13.3 ms on the very driver that wedges the card at <1000 iterations. Use
`BENCH_ITERS` in the thousands before trusting a GPU.

### 2026-08-02 (later) — root cause found: weight upload races VAAPI context creation

Supersedes the "Still open" paragraph above. The forkserver hypothesis is
wrong, and so is the conclusion that the trigger is "how Frigate drives the
detector" — the detector never runs a single inference before it dies.

**The hang is inside `load_model()`, not inference.** Frigate's detector on a
freshly booted card:

```
10:09:37.46  ncnn: using vulkan device 0 (AMD Radeon VII (RADV VEGA20))   <- load_model() begins
10:09:41     amdgpu: ring gfx timeout, signaled seq=3, emitted seq=4      <- weight upload never retires
10:09:42.61  ncnn: loaded yolov9s-320.ncnn.param (vulkan=True)            <- returns, after the reset
10:09:45+    ncnn: extract failed with -1                                 <- forever
```

Ring attribution, measured from `/sys/kernel/debug/dri/1/amdgpu_fence_info`
rather than inferred:

- 200 ncnn inferences advance the **gfx** ring by exactly **2** — both from
  `Net::load_model()`'s weight upload. Per-inference gfx cost is **zero**;
  inference runs entirely on the `comp_1.x.x` rings.
- Live 2-camera VAAPI decoding leaves the gfx ring at **0**. VAAPI does not use
  the gfx ring, so any gfx traffic on this host is ncnn's weight upload.

ncnn's banner explains why: `queueC=1[4] queueT=0[1]`. Compute goes to family 1
(the ACE rings); the *transfer* queue is family 0, which under RADV is the
**graphics** family. `VkTransfer` weight upload is therefore the only thing
ncnn ever puts on the gfx ring.

**The fault, from the AMDGPU device coredump** (`devcoredump/data`, captured
live — it expires in ~5 minutes, so grab it during the reset window):

```
Ring timed out details
IP Type: 0 Ring Name: gfx

[gfxhub] Page fault observed
Faulty page starting at address: 0x0000000000000000
```

with `mmCP_RB_RPTR 0x5c8` against `mmCP_RB_WPTR 0x600` — the command processor
stalled part-way through the ring buffer. A **page fault at address 0** means
the submitted IB referenced an unmapped GPU address; this is a null-address bug
in the upload path, not a runaway shader or a thermal/power fault.

**Controls.** All in the *same* Frigate image, same model, same card, varying
one thing at a time:

| Condition | `load_model()` | Result |
|---|---:|---|
| idle card, plain process | 1.60 s | 2000 iterations pass |
| idle card, forkserver + Frigate's preloads + `nice(0)` | 1.50 s | 500 iterations pass |
| 8 CPU hogs, Frigate running steady | 4.08 s | 300 iterations pass |
| concurrent with Frigate startup | 5.03 s | **gfx ring timeout** |
| concurrent ffmpeg VAAPI churn, **no Frigate at all** | 5.00 s | **gfx ring timeout** |

**Root cause.** ncnn's Vulkan weight upload faults the gfx ring when VAAPI
contexts are being **created/destroyed concurrently** on the same Vega 20 GPU.
Frigate is incidental: it starts nine ffmpeg VAAPI processes at the same moment
it starts the detector, which is exactly the race window. The minimal
reproduction needs no Frigate — spawn short-lived `ffmpeg -hwaccel vaapi`
processes in a loop and call `load_model()` against the Vulkan device.

Note the distinction from the earlier "concurrent VAAPI decode" elimination:
VAAPI already **running steadily** is harmless, because the contexts are
already built. It is context *setup/teardown* overlapping the weight upload
that faults.

**Further hypotheses eliminated this session,** each by direct test:

- *forkserver.* A forkserver child with Frigate's exact preload list and
  `os.nice(0)` loads and runs 500 iterations clean on an idle card. It also
  inherits **no** `/dev/dri` fds, contradicting the "two fds" note above.
- *Mesa build mismatch.* Real and previously unnoticed — the Frigate image
  carries `25.0.7-2~bpo12+1` built against **LLVM 15** (llvmpipe reports
  15.0.6), while every passing standalone soak used the smoke image's
  `25.0.7-2+deb13u1` against **LLVM 19**. Both were recorded as "Mesa 25.0.7".
  It is *not* the cause — the backports build passes 2000 iterations on an idle
  card — but past comparisons between "Frigate" and "standalone" were never
  holding the RADV binary constant. The image also still has
  `libglapi-mesa 22.3.6` beside a 25.0.7 stack.
- *CPU load / slow upload.* Eight busy loops stretch the upload to 4.08 s
  without faulting, so latency alone is not the trigger.
- *Visible VRAM exhaustion.* Plausible on paper — the card has only 256 MB
  CPU-visible VRAM and `rebar=0` — but visible VRAM peaks at **10 MB of 256 MB**
  through a full Frigate start. Not memory pressure.
- *A card left degraded by an earlier reset.* The card passes a 300-iteration
  run immediately after a BACO reset, so post-reset runs are not automatically
  contaminated.

**Correction to the earlier entry.** "25.0.7 still hangs under Frigate, the
driver upgrade is necessary but not sufficient" is right about the symptom but
wrong about the mechanism, and it rests on runs that also differed in RADV
build. Also treat "removing `hwaccel_args` entirely does not stop the hang" as
unverified: go2rtc still runs its own ffmpeg processes, so removing Frigate's
`hwaccel_args` does not actually remove VAAPI context churn.

**Note on the existing smoke gate.** Iteration count is not what it misses.
The gate loads the model on an idle card, which is the one condition that never
fails. To gate this fault the harness has to call `load_model()` *while* VAAPI
contexts are churning; `BENCH_ITERS` in the thousands is still worth having,
but it would not have caught this.

### 2026-08-02 — fix: move the weight upload off the gfx ring

**`RADV_PERFTEST=transfer_queue`.** RADV exposes an SDMA transfer queue family
behind this flag. With it set, ncnn reports `queueT=2[2]` instead of
`queueT=0[1]`, so `load_model()`'s weight upload leaves the graphics family
entirely and the gfx ring is never touched. Set in
`profiles/gfx906-vulkan.env` and passed through by `compose.yaml` to both the
smoke and Frigate services, so the gate exercises the same queue mapping as
production.

Validated against the minimal reproduction — ffmpeg VAAPI churn concurrent
with `load_model()` — which fails **2/2** without the flag and passes **4/4**
with it, gfx ring never advancing (`0x0c → 0x0c`) and `load_model()` back to
~1.5 s from ~5.0 s.

**Plugin changes** (`docker/frigate/ncnn.py`):

- `detect_raw` no longer returns a zero array on failure. It raises
  `NcnnDeviceLost`, which Frigate does not catch, so the detector process exits
  and is restarted with a fresh device. The old fallback is precisely why a
  dead GPU presented as `detection_fps` counting normally with nothing detected.
- `_verify_device()` runs one warm-up inference at the end of `__init__`.
  `load_model()` returns success even when its upload has just faulted the GPU,
  so this is the earliest point the lost device is observable.

**Live result on `noyan`,** first working Frigate GPU inference on gfx906:

```
ncnn: loaded /config/model_cache/yolov9s-320.ncnn.param (vulkan=True)   (1.6 s)
ncnn: device verified with a warm-up inference
```

Stable over 3+ minutes: detector pid unchanged, `inference_speed` 24–29 ms,
`detection_fps` tracking motion, gfx ring still `0x0c`, no new ring timeouts.
The gap between that 24–29 ms and the ~12 ms standalone figure is the
duty-cycle measurement regime, not a regression — Frigate averages in the idle
gaps between bursts.

> **Superseded in part.** The duty-cycle explanation is not the main term. The
> ~12 ms standalone figure was measured in the card's boosted regime; sustained
> standalone inference of the same model on `auto` clocks is 17–19 ms, which is
> most of the way to Frigate's 24–29 ms. See "SMU downclocking" below.

**Deployment status: experimental, currently running on `noyan`** with the
`ncnn` detector, `yolov9s-320`, and `RADV_PERFTEST=transfer_queue` in
`docker-compose.yml`. Backups from the switch: `~/config/config.yaml.bak-preNcnn-1009`
and `~/docker-compose.yml.bak-preRadv`. A full container restart cycle
reloads cleanly (fresh detector, warm-up verified, no gfx traffic). Still to
confirm before promoting: a multi-hour soak.

### 2026-08-02 (later) — YOLOv9 base-model comparison on gfx906

Frigate stopped for the duration so nothing else touched the GPU. Host `noyan`,
AMD Radeon VII (RADV VEGA20, PCI `0000:03:00.0`), kernel 7.1.3, smoke image
`rocm-legacy/vulkan-smoke:gfx906-vulkan-trixie` with Mesa `25.0.7-2+deb13u1`
against LLVM `19.1.7` and `RADV_PERFTEST=transfer_queue`. All models are
Frigate+ base models pulled fresh from the Frigate+ API, converted with pnnx
`20260526` `fp16=0` by `scripts/convert_plus_onnx.sh`, and run with ncnn's three
fp16 options explicitly `False`. Generation `2026.2` carries **46** classes;
the deployed `2025.3` model carries 41.

Measured with `scripts/bench_steady.py`, 2000 iterations per model. It reports
the last half of the run separately from the first quarter, because a single
mean over the whole run mixes two regimes on this card — see below.

**Clocks pinned (`power_dpm_force_performance_level=high`).** This is the
comparable, reproducible number: every run is flat, head ≈ steady, p95 within
~5% of the median.

| Model | Gen | Res | Steady mean ms | Median ms | p95 ms | Raw FPS | Max CPU/GPU error |
|---|---|---:|---:|---:|---:|---:|---:|
| YOLOv9-t | 2026.2 | 320 | 9.19 | 9.09 | 9.69 | 108.8 | 0.00094604 |
| YOLOv9-s | 2026.2 | 320 | 12.00 | 11.93 | 12.19 | 83.3 | 0.0063477 |
| YOLOv9-s | 2025.3 | 320 | 11.75 | 11.71 | 12.15 | 85.1 | 0.00091553 |
| YOLOv9-t | 2026.2 | 640 | 13.45 | 13.38 | 13.93 | 74.3 | 0.0015564 |
| YOLOv9-s | 2026.2 | 640 | 19.41 | 19.22 | 19.70 | 51.5 | 0.0058746 |

Every parity check passed the `< 1e-2` gate. `load_model()` was 1.44–1.47 s for
all five. Output shapes confirm the geometry actually took effect: `(50, 2100)`
at 320 and `(50, 8400)` at 640 for the 46-class models, `(45, 2100)` for the
41-class one.

The 2025.3 and 2026.2 YOLOv9-s 320 models are within 2% of each other, so the
generation bump and its five extra classes cost nothing measurable. Compare
sizes and resolutions across generations freely; compare against the gfx803
table only with care, since that one was measured before the two-regime problem
below was understood.

**SMU downclocking — the reason a single mean is not trustworthy here.** On
default `auto` power management the card does not hold its boost clock under
this workload. `pp_dpm_sclk` alternates between 1801 MHz and 808 MHz while
`gpu_busy_percent` reads 90–99%, and inference latency moves with it by roughly
2x. Temperatures are not involved: edge 52–68 °C, junction 60–83 °C, power
30–158 W against a much higher cap, fan never spinning up. It is the SMU's
activity heuristic, not a thermal or power limit — plausibly because ncnn's
inference runs entirely on the `comp_1.x.x` compute rings and leaves the gfx
ring idle, which is exactly what the earlier ring attribution measured.

Same five models, same harness, `auto` clocks, two consecutive passes:

| Model | Gen | Res | Steady mean ms, pass 1 | pass 2 | Pinned, for reference |
|---|---|---:|---:|---:|---:|
| YOLOv9-t | 2026.2 | 320 | 17.61 | 18.59 | 9.19 |
| YOLOv9-s | 2026.2 | 320 | 18.16 | 14.27 | 12.00 |
| YOLOv9-s | 2025.3 | 320 | 19.44 | 17.02 | 11.75 |
| YOLOv9-t | 2026.2 | 640 | 24.61 | 25.07 | 13.45 |
| YOLOv9-s | 2026.2 | 640 | 30.81 | 30.88 | 19.41 |

Note both the penalty and the spread: YOLOv9-s 320 2026.2 differs by 27%
between two back-to-back passes of the same binary on the same card. Any
benchmark of this GPU that does not pin the performance level is reporting
where the SMU happened to sit.

This also corrects the earlier reading of the live Frigate figure. Standalone
`yolov9s-320` on `auto` sustains 17–19 ms, not the ~12 ms recorded before, so
Frigate's 24–29 ms is mostly the same downclocking rather than duty-cycle
averaging. Duty cycle may still account for the remainder.

**Recommendation.** YOLOv9-s 640 is affordable on this card and is the
accuracy upgrade over what is deployed: 19.4 ms pinned, 30.9 ms on `auto`,
against a demand of 20 detections/s from two 10 fps cameras — a 50 ms budget
per detection. If the performance level stays on `auto` and more headroom is
wanted, YOLOv9-t 640 costs about what the current 320 model already costs in
practice (~25 ms) while quadrupling input pixels. YOLOv9-t 320 is only worth
choosing if camera count grows substantially.

Pinning the performance level is worth roughly a 2x latency win but holds the
card at full clocks continuously, which raises idle power on a machine that
otherwise runtime-suspends the GPU between bursts. It was set only for the
duration of these measurements and restored to `auto` afterwards; adopting it
in production is a separate decision.

**Artifacts** in `~/rocm-legacy/models` on `noyan`, sha256 (first 16):

| Model | .param | .bin |
|---|---|---|
| yolov9t-320-2026-2 | `fc54ffc696305842` | `3cde274f9424935d` |
| yolov9s-320-2026-2 | `e72b72505b15d9a4` | `dd2dbc319e014acb` |
| yolov9t-640-2026-2 | `d881db4a2e98b4c2` | `3ea1bc76bd8d5a72` |
| yolov9s-640-2026-2 | `41c0b0fba2d8134e` | `400023db21f21ad2` |

Frigate was restarted afterwards and came back clean: model loaded in 0.45 s,
warm-up inference verified, both cameras at 10 fps, gfx ring still `0x0c`, and
no ring timeout newer than the two from 10:17 and 10:21 that predate this run.

### 2026-08-02 (later) — switched the live detector to YOLOv9-s 640

`~/config/config.yaml` on `noyan` now points at
`/config/model_cache/yolov9s-640-2026-2.ncnn.param` at 640x640 with the
46-class labelmap. Backup: `~/config/config.yaml.bak-preS640-1127`. All ten
tracked objects exist in the new labelmap, and Frigate logged no unsupported
object warnings.

Startup is clean — model loaded in 0.45 s, warm-up inference verified, detector
pid stable across 8+ minutes, gfx ring still `0x0c`, no new ring timeouts.

**It is running close to its limit, though.** Live `inference_speed` settles at
**40–48 ms**, well above the 30.9 ms this model measured standalone on `auto`
clocks. At ~44 ms one detector process sustains ~23 detections/s against a
worst-case demand of 20/s from two 10 fps cameras, and `skipped_fps` does reach
**1–3** when both cameras have motion at once. Detection is working; frames are
occasionally being dropped at peak.

Levers, if the skipping matters:

- *Pin the performance level.* A brief live test with `high` set showed
  `inference_speed` trending toward ~37 ms with `skipped_fps` at 0, but motion
  load varied across the samples so this is suggestive, not a controlled
  result. Standalone the same change is worth 30.9 → 19.4 ms.
- *Run a second `ncnn` detector instance.* Doubles detector throughput; the
  28 MB of weights is nothing against 16 GB of VRAM, and the GPU has headroom.
  Untested here.
- *Fall back to YOLOv9-t 640,* which is ~6 ms cheaper standalone and keeps the
  4x resolution gain over the previous 320 model.

The performance level was restored to `auto` after the test, so the deployment
as it stands is on stock power management.

### 2026-08-02 (later) — a second detector does not help; clock pinning does

**Second `ncnn` detector instance: no benefit, reverted.** Two detectors
(`ncnn` and `ncnn_2`) both started cleanly — including two concurrent
`load_model()` calls during ffmpeg VAAPI startup, the exact race window from
the gfx-ring investigation, with no fault, which is further confirmation that
`RADV_PERFTEST=transfer_queue` holds. But per-detector `inference_speed` rose
from ~44 ms to **48–52 ms**, and `skipped_fps` at comparable `detection_fps`
was unchanged or slightly worse (2.7–3.2 at ~17–20 det/s, versus 1.0–3.1 for a
single detector at the same load). Aggregate capacity nearly doubled and the
skipping did not improve, so detector throughput was never the constraint.

**The constraint is the GPU clock.** Under live Frigate the host is 79% idle
(load 1.0 across 8 threads), so CPU is not the limit either — but
`pp_dpm_sclk` reads **808 MHz** during normal operation, with
`gpu_busy_percent` around 22%. Frigate's duty cycle is low enough that the SMU
never ramps the card up, so every live inference pays the downclocked rate.
This is the same effect as the standalone benchmarks, now confirmed in
production.

**Paired comparison, single detector, same model, alternating windows:**

| Performance level | `inference_speed` | `skipped_fps` at peak (~21 det/s) | sclk |
|---|---:|---:|---|
| `auto` | 41.5–48.9 ms (mean ~45.6) | 3.7 | mostly 808 MHz |
| `high` | 32.0–42.6 ms (mean ~38.2) | 0.0 | 1801 MHz throughout |

Pinning is worth ~16% on mean latency, and more importantly it holds up at peak
demand: at ~21 detections/s — both cameras in motion, the worst case for two
10 fps feeds — `auto` dropped 3.7 fps of frames and `high` dropped none.

Unexplained: even pinned, live `inference_speed` is ~38 ms against 19.4 ms
standalone for the same model at the same clocks. Frigate-side per-call
overhead and duty-cycle ramp presumably account for it, but this has not been
measured.

**Current state on `noyan`:** single `ncnn` detector, `yolov9s-640-2026-2`,
performance level restored to `auto`. Config backups from this sequence:
`config.yaml.bak-preS640-1127` and `config.yaml.bak-preDual-1141`. Pinning has
not been made persistent; it would need a boot-time unit or udev rule to
survive a reboot.

### 2026-08-02 (later) — what clock pinning actually costs

Paired measurement under live Frigate, single detector, `yolov9s-640-2026-2`,
30 samples 10 s apart per condition (`scripts/power_compare.sh`, raw data in
`logs/power-compare.txt` on `noyan`).

| | `auto` | `high` | Delta |
|---|---:|---:|---:|
| sclk mean | 1000 MHz (at max 13% of samples) | 1801 MHz (100%) | pinned |
| Board power, mean | 43.8 W | 63.0 W | **+19.2 W** |
| Board power, median | 23 W | 38 W | **+15 W** |
| Board power, min | 20 W | 35 W | +15 W |
| Edge temp, mean / max | 53.2 / 54 °C | 58.7 / 62 °C | +5.5 / +8 °C |
| Junction temp, mean / max | 56.6 / 60 °C | 65.7 / 74 °C | +9.1 / +14 °C |
| Memory temp, mean / max | 53.9 / 55 °C | 59.0 / 62 °C | +5.1 / +7 °C |
| Fan | 0 RPM | 0 RPM | none |
| `inference_speed`, mean | 44.9 ms | 37.5 ms | **−16%** |
| `skipped_fps`, mean / max | 1.77 / 5.9 | 0.60 / 1.9 | **−66%** |
| Samples with any skipping | 24/30 | 16/30 | — |

The detection comparison is conservative: the `high` window happened to carry
*more* load (mean 17.3 det/s, peak 30.8) than the `auto` window (14.7, peak
21.0), and still skipped a third as much.

**Thermals are a non-issue on this card.** Limits are edge 100 °C, junction
110 °C, memory 94 °C, with a 250 W cap. Pinned peaks were 62 / 74 / 62 °C and
160 W — 36 °C of junction headroom and 90 W of power headroom. `pwm1_enable`
is 2 (automatic) and the fan stayed at 0 RPM in *both* conditions, so the card
never left zero-RPM mode and there is no acoustic difference.

**Power is the real cost.** +19.2 W mean continuous is ~168 kWh/year; on the
median it is +15 W, ~131 kWh/year. The proportional cost is worst when idle:
the floor rises from 20 W to 35 W, so a quiet night costs ~75% more GPU power
for no benefit, since nothing is being detected.

Runtime suspend was never observed (0/30 in both conditions), but the 10 s
polling interval itself resumes the device, so this measurement cannot say
whether pinning would prevent suspend during a genuinely idle period.

**Summary of the tradeoff:** ~15–19 W continuous and ~9 °C junction, against a
16% latency cut and roughly a third of the dropped frames at peak. No thermal
or acoustic downside. If the goal is not missing frames when both cameras have
motion at once, pinning buys that; if dropped frames at peak are acceptable,
`auto` is the cheaper setting.

### 2026-08-02 (later) — dynamic pinning: works, saved nothing by daylight

`scripts/dynamic_clock_pin.py` polls Frigate's `/api/stats` every 5 s and holds
`high` while `detection_fps >= 6` or any frames are being skipped, releasing to
`auto` after 12 consecutive polls (60 s) below 3 det/s. It restores `auto` on
exit, including on SIGTERM, and holds its current level rather than guessing if
Frigate is unreachable.

The mechanism works — it pinned within one poll of startup and `inference_speed`
fell from 48.8 ms to ~30 ms immediately. But over a 19.8 minute midday run:

| | Dynamic | Static `high` | Static `auto` |
|---|---:|---:|---:|
| Pinned duty cycle | **100%** | 100% | 0% |
| `inference_speed`, mean | 38.1 ms | 37.5 ms | 44.9 ms |
| `skipped_fps`, mean | 0.32 | 0.60 | 1.77 |
| Board power, mean / median | 55.5 / 36 W | 63.0 / 38 W | 43.8 / 23 W |

**It never released once.** Only 8 of 119 samples were below the 3 det/s release
threshold, and never 12 in a row. With two outdoor cameras in daylight this site
effectively always has something moving: mean 12.9 det/s, and below 6 det/s in
only 23 of 119 samples. During active hours the governor is *identical* to
static pinning — same clocks, same power, same benefit, no saving.

Its entire value is therefore in quiet periods, and a midday window contains
none. Quantifying it needs a 24 h run where overnight is included; the numbers
above should not be read as evidence either way about the daily average.

Loosening the release rule (higher threshold, shorter dwell) would release more
often, but the cost of being unpinned when a burst starts is exactly the dropped
frames the pinning exists to prevent, so a conservative release is the right
default. The lever worth tuning is the dwell, and only with overnight data.

### 2026-08-02 (later) — the load itself was the problem, not the GPU

The detector was never saturated by two cameras' worth of real activity. All of
it came from `reolink_west`, whose view is dominated by wind-blown foliage — an
overhanging rowan branch across the top and a large berry bush on the right.
`reolink_south` sat at **0.0 det/s** throughout every measurement above.

Changes applied to `~/config/config.yaml`:

- `reolink_west`: `detect.fps` 10 → 5, `motion.threshold` 30 → 35,
  `motion.contour_area` 10 → 30. `contour_area` is the targeted lever — motion
  runs on a ~100 px-tall frame, so 30 ignores leaf flicker while a person
  anywhere in that yard is an order of magnitude larger.
- `reolink_south`: `detect.fps` 10 → 5 only. It generates no load, so its
  motion settings were left at defaults.

Backup: `config.yaml.bak-preMotion-1255`.

Effect: `detection_fps` went from a mean of 12.9 to **0.0 sustained**, and the
dynamic governor began releasing to `auto` for the first time (duty fell from
100% to 86% within minutes). Peak worst-case demand is now 10 det/s rather than
20, which changes the pinning calculus entirely — at half the frame rate and a
fraction of the spurious motion, the ~38 ms pinned latency has roughly four
times the headroom it did.

**Attribution is ambiguous.** A new 12-point motion mask covering the top-right
vegetation was saved from the Frigate UI in the same window as these changes, so
how much of the drop is the mask versus the motion tuning is not separable from
this data. Both are in effect.

**Verified:** a person was detected on `reolink_west` shortly after the change,
so the tuning is not suppressing real subjects — 0.0 det/s between events is the
intended result, not a blinded camera.
