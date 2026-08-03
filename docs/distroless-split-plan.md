# Distroless split-pod plan

Where this project is going after the Python 3.13 slim image, and why. Written
2026-08-02, at the point where `cvandesande/frigate-vulkan:py313-20260802` went into
an overnight soak on tirnanog. Revised 2026-08-03 against the actual nginx config and
s6 tree carried in that image, which resolved the open questions and turned up two
cross-container dependencies the first draft missed. docker-compose is treated as a
co-equal target throughout, not a secondary one.

## Goal

Three purpose-built containers on distroless bases, built from source, with no
dependency on `ghcr.io/blakeblackshear/frigate` as a donor image.

Longer term the goal is to stop being a Frigate derivative at all: an own Rust/Leptos
UI, then a Rust NVR behind it, with the ncnn/Vulkan detector as the part that was always
the point. That work lives in a separate repo, `corvette`; this one keeps the detector
plugin and the packaging. The arc is staged in "Roadmap" below and does not change the
packaging work described here -- the split pod is what makes it possible to replace one
component at a time.

## Decisions

**Split the pod; Kubernetes is the supervisor.** `frigate`, `go2rtc` and `nginx`
become separate containers in one pod, sharing the network namespace so the existing
`127.0.0.1` wiring is unchanged. This retires s6-overlay, whose 17
`#!/command/with-contenv bash` run scripts are what force `bash`, `yq`, `jq`,
`mountpoint` and the `openssl` CLI into the image.

**No donor image.** Each component is built or downloaded directly. Under the split,
most of what the donor supplied is deleted rather than rebuilt -- see the table below.
The three images stay in one Dockerfile with three targets, as `docker/Dockerfile` does
today, so the shared ARGs and base stages are declared once.

**Same topology under docker-compose.** Compose has no ingress, so nginx is required
there regardless; diverging would mean maintaining two architectures. Compose is a
first-class target, not a downgrade path -- every Kubernetes construct the split needs
has a direct compose equivalent, worked through under "Docker-compose topology" below.
The same three images run in both.

**Certificates are externally managed; the image never touches one.** Kubernetes
terminates at the ingress and the pod nginx carries no certificate. noyan already runs
certbot with a DNS-01 plugin on the host, so nginx mounts the result read-only.
Everything else mounts its own or serves plain HTTP. Nothing in the image issues,
generates, renews or watches a certificate -- which is what deletes both `certsync` and
the `openssl` CLI, and what keeps the ACME module out of the build.

**nginx stays in the pod.** The ingress (`nginx.org/ingress-controller`,
orkcams.opendmz.com) takes over TLS and routing only. It cannot replace nginx because
Frigate's build runs `nginx-vod-module` for on-the-fly HLS repackaging of recordings
(`location /vod/`, `vod hls`, fmp4, `vod_mode mapped` calling back to `/api`, 512m
metadata cache), `secure_token` signed URLs, and serves `/clips/`, `/recordings/`,
`/exports/` from `/media/frigate` plus `/stream/`, `/cache/` from `/tmp`. An ingress
controller has neither the vod module nor access to the media volume.

**`auth_request` stays in the pod nginx.** Authorization stays a config property of
the pod rather than a NetworkPolicy question, and behaves identically under compose.
`--with-http_auth_request_module` and the `auth_request.conf` includes already exist.

**The web UI ships in the nginx container, not frigate.** `location /` is
`root /opt/frigate/web` -- nginx is the only process that reads it, so the UI build
stage feeds the nginx image and the frigate image never carries it. That build is
trunk/wasm rather than node/pnpm, because the UI is being replaced with a Rust/Leptos
app rather than rebuilt from upstream; see the roadmap.

**`FRIGATE_VERSION` pins the Python only.** It was going to have to pin the web UI
too -- same tag or the two drift against each other's API contract -- but the UI is
being replaced with a Rust/Leptos app (see the roadmap), so that coupling never
materialises. Pinned to 0.17.2 to match the donor being retired; bumping is a deliberate
one-line change plus a soak, the same handling `NCNN_TAG` gets today.

## Process and port map

Everything binds loopback in the shared network namespace, so the split is invisible
to the wiring. Nothing collides:

| Container | Port | Purpose |
| --- | --- | --- |
| nginx | 5000 | internal HTTP (`listen.conf`) |
| nginx | 8971 | external, auth-protected (`listen.conf`); TLS on noyan, plain in Kubernetes |
| frigate | 5001 | API -- `upstream frigate_api`, and `auth_location.conf`'s `/auth` |
| frigate | 5002 | MQTT websocket -- `upstream mqtt_ws`, `frigate/comms/ws.py` |
| frigate | 8082 | jsmpeg -- `upstream jsmpeg`, `frigate/output/output.py` |
| go2rtc | 1984 | API -- `upstream go2rtc`, `go2rtc_upstream.conf` |
| go2rtc | 8554 | RTSP |
| go2rtc | 8555/tcp+udp | WebRTC |

Every nginx upstream is a literal `server 127.0.0.1:<port>`, not a hostname. That
matters for start ordering -- see below.

## The go2rtc config handoff

This is the one hard cross-container dependency, and the first draft did not mention
it. go2rtc does not read `/config/config.yml`. Frigate's
`/usr/local/go2rtc/create_config.py` renders it to **`/dev/shm/go2rtc.yaml`**, and
go2rtc is exec'd as:

```
go2rtc -config=/config/go2rtc_homekit.yml -config=/dev/shm/go2rtc.yaml
```

Two consequences the pod spec has to answer:

- **`create_config.py` is not standalone.** It does `sys.path.insert(0, "/opt/frigate")`
  and imports `frigate.const`, `frigate.ffmpeg_presets`, `frigate.util.config` and
  `frigate.util.services`. Putting it in the go2rtc container would drag the entire
  Frigate package in and defeat the split.
- **`/dev/shm` is per-container by default.** Today both processes share one namespace,
  so the file just appears. Split, it does not.

The resolution is an **initContainer built from the frigate image** that runs
`create_config.py` and writes into an `emptyDir{medium: Memory}` mounted at `/dev/shm`
in both the init and the go2rtc containers. initContainers run to completion before
any app container starts, which gives the ordering guarantee natively without needing
native sidecars. Under compose the equivalent is a one-shot service with
`depends_on: {condition: service_completed_successfully}`.

While doing this, drop the `LIBAVFORMAT_VERSION_MAJOR` probe. Both the frigate and
go2rtc run scripts currently shell out to `ffmpeg -version | grep -Po` to compute it.
ffmpeg is pinned to 7.0, whose libavformat is **61**, so it becomes a static `ENV` in
both images and no `grep`/`sed` is needed at start.

**The go2rtc container needs its own ffmpeg, at the same absolute path.** The rendered
config sets `ffmpeg.bin` to `/usr/lib/ffmpeg/7.0/bin/ffmpeg`, and with
`birdseye.restream` enabled it emits a stream whose source is a literal
`exec:<ffmpeg> ... -i /tmp/cache/birdseye ... -f rtsp` built by
`parse_preset_hardware_acceleration_encode`. So go2rtc executes ffmpeg with the user's
`hwaccel_args`, which means the go2rtc image also needs the VA-API stack
(`libva2`, `libva-drm2`, `mesa-va-drivers`) and the container needs `/dev/dri` plus the
video/render groups. It does *not* need Vulkan or ncnn -- that is detector-side and
stays in frigate.

## What the donor supplies today, and what replaces it

| Donor piece | Replacement |
| --- | --- |
| `/init`, `/command`, `/package`, `/etc/s6-overlay` | deleted -- Kubernetes supervises |
| `yq`, `jq`, `openssl` CLI, `mountpoint` | deleted -- only s6 scripts used them |
| `tempio` + `get_listen_settings.py` / `get_base_path.py` | deleted -- but `listen.conf` and `base_path.conf` must be materialized statically, see below |
| `certsync` service | deleted -- it exists only to `nginx -s reload` when the cert on 8971 drifts from `/etc/letsencrypt`; `ssl_certificate_cache` makes rotation self-healing |
| `prepare` run script | deleted -- two one-time migrations (HA add-on dir, db from `/media` to `/config`) plus an `rm -f /dev/shm/.frigate-is-stopping` that moves into the frigate entrypoint |
| `go2rtc-healthcheck` service | deleted -- a `livenessProbe` on `:1984/api/streams` replaces the 30s curl loop and `s6-svc -t` |
| ICU 72 shim libraries | deleted -- only needed because the donor nginx was Bookworm-built |
| `go2rtc` | upstream release binary (static) |
| `vec0.so` | `asg017/sqlite-vec` release |
| labelmaps, CPU tflite models | files from the Frigate repo |
| `ffmpeg` | prebuilt (BtbN / jellyfin-ffmpeg) or own build -- **in both frigate and go2rtc**, at the identical path |
| Frigate package + migrations | `git clone` at `${FRIGATE_VERSION}`, pinned to 0.17.2 |
| Web UI (21MB) | **replaced, not rebuilt** -- own Rust/Leptos app, trunk/wasm build stage, output into the nginx image |
| nginx | **new work** -- but see below |

## nginx build

Frigate's nginx is configured with `ngx_devel_kit`, `nginx-set-misc-module`,
`nginx-vod-module`, `nginx-secure-token-module`, plus `--with-http_ssl_module`,
`--with-http_auth_request_module`, `--with-http_sub_module`,
`--with-http_realip_module`, `--with-threads`, `--with-file-aio`.

That is the whole module list -- no ACME module, since certificates are externally
managed (see "TLS"). No Rust toolchain, no `--with-compat`.

`~/dockers/nginx/Dockerfile` already builds nginx + OpenSSL from source, multi-arch
with native cross-compilation, pinned by version + sha256 ARG, ending in a distroless
stage. That is the template; swap the ngx-otel-rust module for the four above, which
also drops Rust from the build stage entirely.

`nginx-vod-module` is not optional -- it is what serves recording playback.
`--with-threads` and `--with-file-aio` are likewise load-bearing: the server block sets
`aio on`, `aio threads` inside `/vod/`, and `vod_open_file_thread_pool default`.

nginx must be **1.27.4 or newer** for `ssl_certificate_cache`, which is what lets a
certbot-renewed certificate be picked up without a reload. The template pins current
mainline, so this is a floor to assert, not a change.

Three things the run script does at runtime that must become build-time or static,
because the point of distroless is a read-only rootfs with no shell:

- **`listen.conf`** is tempio-rendered from `listen.gotmpl`. For Kubernetes it collapses
  to `listen 5000;` and `listen 8971;` -- the whole `ssl_*` block and `/etc/letsencrypt`
  go away. The `location /.well-known/acme-challenge/` block goes too -- nothing in the
  image answers ACME challenges any more. The TLS variant becomes a small overlay
  adding `listen 8971 ssl` and the certificate pair; see "TLS: externally managed
  certificates only".
- **`base_path.conf`** is tempio-rendered from `base_path.gotmpl` and is empty unless
  `FRIGATE_BASE_PATH` is set. Ship an empty file; regenerate only if we ever need a
  base path.
- **`worker_processes`** is patched by `sed -i` on `nginx.conf` at start, clamping
  `auto` to at most 4. Set it statically instead.

**nginx runs nonroot.** Both listen ports are above 1024, so no `NET_BIND_SERVICE` is
needed. Consequences to build in:

- **Drop `user root;` from `nginx.conf`.** When the master does not start as root the
  directive is ignored with a warning; workers inherit the master's uid.
- **Three writable paths need to be owned by that uid**: `/dev/shm/nginx_cache`
  (`proxy_cache_path`, 10m keys / 10m max), `/run` for the pid file, and the
  `open_file_cache` state. In Kubernetes that is `runAsUser` plus `fsGroup`; under
  compose it is `user:` plus ownership on the volumes. Both are `emptyDir`/tmpfs, so
  they are created fresh each start and cannot simply inherit image ownership.
- **The certificate must be staged for that uid** -- the one real cost of this choice,
  since it puts a `--deploy-hook` on every host that terminates TLS. See "TLS".
- The read-only mounts (`/media/frigate`, `/tmp`, the cert dir) need world- or
  group-readable content; `/media/frigate` is written by frigate, so the two containers
  need compatible uid/gid choices.

## Base images: distroless, including frigate

All three containers land on distroless bases. For frigate that means keeping
`/api/vainfo` and the GPU stats panel by carrying the two CLI tools across rather than
dropping the endpoints -- which works, because Frigate never shells out to reach them:

```python
radeontop_command = ["radeontop", "-d", "-", "-l", "1"]   # util/services.py
p = sp.run(radeontop_command, capture_output=True, ...)
```

Both `radeontop` and `vainfo` are invoked as argv lists with no `shell=True`, so
`execve` runs them directly and no `/bin/sh` is required. Two conditions follow: the
binaries must sit on `PATH` (they are invoked by name, not absolute path), and their
shared-library closures must be copied alongside them.

| Tool | Closure | Notes |
| --- | --- | --- |
| `radeontop` | 9 libs | cheap -- `libdrm`/`libdrm_amdgpu` are already needed; adds `libncursesw`, `libtinfo`, `libpciaccess`, `libz` |
| `vainfo` | 18 libs | drags in `libX11`, `libX11-xcb`, `libXau`, `libXdmcp`, `libXext`, `libXfixes`, `libxcb`, `libxcb-dri3`, `libwayland-client`, `libffi` alongside the four `libva*` |

`vainfo` links the X11 and Wayland backends unconditionally, so they must all be
present even though Frigate calls it as `vainfo --display drm --device /dev/dri/...`.
Still small, but it is the reason the frigate image cannot be quite as thin as the
other two.

Python needs no assembly either. `gcr.io/distroless/python3:nonroot` is **Debian 13
(trixie), Python 3.13.5, uid 65532** -- the same Debian generation as the
`python:3.13-slim-trixie` builder, so glibc matches and the ncnn wheel and every other
compiled extension built there run unmodified. It also arrives nonroot at exactly the
uid the nginx decision wants. The interpreter is inherited, not rebuilt; only
`dist-packages` and the GPU libraries are copied in.

Four details of that base that change the Dockerfile:

- **`dist-packages`, not `site-packages`.** The base has prefix `/usr` with the
  interpreter at `/usr/bin/python3.13`, while the builder has prefix `/usr/local`.
  Copy the builder's `/usr/local/lib/python3.13/site-packages` to
  `/usr/local/lib/python3.13/dist-packages` -- Debian's `site.py` adds that path
  automatically once it exists, so no `PYTHONPATH` is needed. Verified: a module
  dropped there imports with no further configuration. Note this also moves the
  `tflite_runtime` shim, which `docker/Dockerfile.py313` currently writes to
  `/usr/local/lib/python3.13/site-packages/tflite_runtime`.
- **The image ships no `curl`,** so the `HEALTHCHECK` in `docker/Dockerfile.py313` --
  already needing its port corrected from 5000 to 5001 -- has to be rewritten against
  `urllib.request` instead. Python is right there, so this is a one-liner, not a
  problem.
- **The entrypoint is `/usr/bin/python3.13`.** Either inherit it and pass
  `CMD ["-u", "-m", "frigate"]`, or override it explicitly; either way `python3` is not
  on `PATH` under the name the s6 scripts used.
- **Pin by digest.** The tag is rolling -- it is 3.13.5 today -- and everything else in
  this build is pinned by version + sha256 ARG. This should match.

So the frigate image is: that base, plus `dist-packages`, plus the Vulkan ICD and
VA-API stack, plus `radeontop`/`vainfo` and their closures. The maintenance surface is
the GPU library closure on a Mesa bump, not the interpreter.

Resolved from the nginx config and the run scripts. `ro` where the container only
reads:

| Path | frigate | go2rtc | nginx | Notes |
| --- | --- | --- | --- | --- |
| `/config` | rw | rw | -- | go2rtc writes back to `go2rtc_homekit.yml`, so it cannot be `ro` |
| `/media/frigate` | rw | -- | **ro** | `root /media/frigate` for `/clips/`, `/recordings/`, `/exports/`; also what `vod_mode mapped` reads after the `/api` callback |
| `/tmp` | rw | **ro** | **ro** | shared across all three, not per-container -- see below |
| `/dev/shm` | rw | ro | rw | see caveat below |
| `/models` | ro | -- | -- | ncnn model + labelmap |
| `/opt/frigate/web` | -- | -- | baked | in the nginx image, not a volume |

`/tmp` has to be shared by all three, for three unrelated reasons. nginx serves
`location /stream/` with `root /tmp` and `/cache/` with `alias /tmp/cache/`, both
written by frigate. And `BIRDSEYE_PIPE` is `/tmp/cache/birdseye` -- a FIFO frigate
writes raw frames into and go2rtc's ffmpeg reads with `-i`, whenever
`birdseye.restream` is on. Opening a FIFO for reading works fine through a read-only
bind mount, so `ro` is correct for both consumers.

The `/dev/shm` row is the awkward one. Four different things live there and they do
not want the same sharing:

- `go2rtc.yaml` -- written by the init container, read by go2rtc.
- Frigate's frame buffers -- large, frigate-private, and the reason
  `UntrackedSharedMemory` matters (see constraints below).
- `.frigate-is-stopping` -- written by frigate, read by its own healthcheck.
- `nginx_cache` -- nginx-private.

Only `go2rtc.yaml` genuinely needs to cross a container boundary. Cleaner than one
shared `/dev/shm` for all three: a small dedicated `emptyDir{medium: Memory}` mounted
at `/dev/shm` in the init container and in go2rtc only, with frigate and nginx each
keeping a private `/dev/shm`. It has to be mounted at that path rather than somewhere
tidier because `create_config.py` ends in a hardcoded
`open("/dev/shm/go2rtc.yaml", "w")` -- mounting the shared volume as the init
container's whole `/dev/shm` avoids patching upstream. go2rtc's `-config=` flag is
ours to set, so it points at the same path.

Frigate's private `/dev/shm` needs sizing -- the default 64MB is not enough for its
frame buffers, so the `emptyDir` needs an explicit `sizeLimit` (`shm_size` under
compose).

## Start ordering

Kubernetes does not order app containers within a pod (absent native sidecars), so
each container has to tolerate the others being absent. Checked against the config,
this is mostly free:

- **nginx starting before frigate or go2rtc is fine.** Every upstream is a literal IP
  (`server 127.0.0.1:5001`), so nginx does no startup DNS resolution and does not
  refuse to start. It serves 502 until the backends bind, and recovers with no reload.
  Had upstreams been hostnames this would have needed a `resolver` and runtime
  `proxy_pass` variables; they are not, so it does not.
- **go2rtc before frigate is fine** and is the current order anyway -- the s6 tree has
  `frigate/dependencies.d/go2rtc`, so frigate already starts last.
- **go2rtc before its config exists is not fine.** That is exactly what the
  initContainer above guarantees, and it is the only ordering constraint that needs
  enforcing rather than tolerating.

Note the direction of the s6 dependency chain being replaced: `prepare` → `go2rtc` →
`frigate` → `nginx`. Only the first arrow survives as a real constraint.

## Docker-compose topology

Every construct maps one-to-one, so the two deployments stay the same architecture
rather than diverging:

| Kubernetes | docker-compose |
| --- | --- |
| shared pod network namespace | `network_mode: "service:frigate"` on go2rtc and nginx |
| initContainer | one-shot service + `depends_on: {condition: service_completed_successfully}` |
| `emptyDir{medium: Memory}` | named volume with `driver_opts: {type: tmpfs, device: tmpfs}` |
| `emptyDir` (shared `/tmp`) | plain named volume |
| `sizeLimit` on frigate's `/dev/shm` | `shm_size:` |
| `livenessProbe` | `healthcheck:` + `restart: unless-stopped` |
| ingress terminates TLS | host certbot + `/etc/letsencrypt` mounted read-only -- see below |

`network_mode: "service:frigate"` is what preserves the `127.0.0.1` wiring, and it is
worth being deliberate about. The alternative -- a normal compose network with
`upstream frigate { server frigate:5001; }` -- would mean a second nginx.conf, and it
would reintroduce exactly the startup-resolution failure that literal IPs avoid: nginx
refuses to start when an upstream hostname does not resolve, so a cold `compose up`
would race. Sharing the namespace keeps one config for both deployments.

Sketch, with the parts that matter:

```yaml
services:
  # initContainer equivalent. Renders /dev/shm/go2rtc.yaml, then exits 0.
  go2rtc-config:
    image: ${FRIGATE_VULKAN_IMAGE}
    command: ["python3", "/usr/local/go2rtc/create_config.py"]
    restart: "no"
    environment: [PROFILE_NAME, "FRIGATE_..."]   # create_config.py reads FRIGATE_* vars
    volumes:
      - ./config:/config:ro
      - go2rtc-conf:/dev/shm

  # Owns the network namespace, so every published port is declared here.
  frigate:
    image: ${FRIGATE_VULKAN_IMAGE}
    shm_size: "512mb"
    devices: [/dev/dri]
    group_add: ["${VIDEO_GID:-44}", "${RENDER_GID:-109}"]
    ports: ["5000:5000", "8971:8971", "8554:8554", "8555:8555/tcp", "8555:8555/udp"]
    volumes:
      - ./config:/config
      - ./models:/models:ro
      - ${MEDIA_DIR}:/media/frigate
      - frigate-tmp:/tmp
    healthcheck:
      # No curl in distroless; the interpreter is already the entrypoint.
      test: ["CMD", "/usr/bin/python3.13", "-c",
             "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5001/version')"]

  go2rtc:
    image: ${GO2RTC_IMAGE}
    network_mode: "service:frigate"
    devices: [/dev/dri]                          # birdseye restream execs ffmpeg hwaccel
    group_add: ["${VIDEO_GID:-44}", "${RENDER_GID:-109}"]
    depends_on:
      go2rtc-config: {condition: service_completed_successfully}
    command: ["-config=/config/go2rtc_homekit.yml", "-config=/dev/shm/go2rtc.yaml"]
    volumes:
      - ./config:/config                         # rw: go2rtc writes back go2rtc_homekit.yml
      - go2rtc-conf:/dev/shm:ro
      - frigate-tmp:/tmp:ro                      # /tmp/cache/birdseye FIFO

  nginx:
    image: ${NGINX_IMAGE}
    network_mode: "service:frigate"
    volumes:
      - ${MEDIA_DIR}:/media/frigate:ro
      - frigate-tmp:/tmp:ro
      # Whole tree, not live/<name>/ -- those are symlinks into ../../archive/.
      - /etc/letsencrypt:/etc/letsencrypt:ro

volumes:
  go2rtc-conf:
    driver_opts: {type: tmpfs, device: tmpfs}
  frigate-tmp:
```

Four consequences to design around:

- **Frigate's healthcheck has to move to 5001.** The current `HEALTHCHECK` in
  `docker/Dockerfile.py313` curls `127.0.0.1:5000/api/version`, which is *nginx's*
  port -- correct in one container, wrong once split, since it would report frigate
  unhealthy whenever nginx is down and vice versa. Frigate's own check is
  `127.0.0.1:5001/version` (no `/api` prefix -- that is nginx's
  `rewrite ^/api(/.*)$ $1`). nginx gets its own check on 5000.
- **`depends_on: {frigate: {condition: service_healthy}}` is no longer needed** for
  nginx, which is a relaxation from the first draft of this plan. `network_mode:
  service:frigate` already forces frigate to start first, and nginx tolerates a dead
  backend by design. Waiting for healthy would only delay serving the static UI.
- **The config is not regenerated on a go2rtc-only restart.** s6 re-ran
  `create_config.py` on every go2rtc start; a Kubernetes pod restart re-runs
  initContainers, so both of those stay correct. Compose does not -- `compose restart
  go2rtc` reuses a stale `go2rtc.yaml` after a `config.yml` edit. Decided: keep the
  render a separate service so compose and Kubernetes share one startup path, and
  document `compose up -d --force-recreate go2rtc-config go2rtc` as the reload
  procedure. Folding it into go2rtc's entrypoint would fix the wart at the cost of the
  two deployments diverging.
- **Recreating frigate breaks the netns.** Anything joined with `network_mode:
  service:frigate` has to be recreated alongside it. Normal for this pattern, but it
  means `compose up -d frigate` alone is not a safe operation.

### TLS: externally managed certificates only

**Nothing in the image issues, renews, generates or watches a certificate.** The image
consumes a certificate someone else manages, or it serves plain HTTP. That is the whole
policy, and it covers both deployments:

- **Kubernetes** terminates at the ingress. The pod nginx serves plain HTTP and has no
  certificate at all.
- **noyan** runs Debian 13 with certbot and a DNS-01 plugin already handling issuance
  and renewal on the host. A `--deploy-hook` stages the pair where nginx can read it.
- **Anything else** either mounts its own certificate or serves plain HTTP behind its
  own proxy.

`ngx_http_acme_module` is **not** built. It cannot serve noyan -- it supports `http-01`
(port 80) and `tls-alpn-01` (port 443) only, both blocked there, and DNS-01 is
[nginx/nginx-acme#11](https://github.com/nginx/nginx-acme/issues/11), open with no
timeline. Kubernetes does not need it either. Dropping it takes `--with-compat`, the
Rust dependency and the `acme-state` volume out of the plan with it. This also settles
`certsync`: deleted, not reimplemented, because there is no longer any in-image actor
that could care when a certificate changes.

Renewal is the only interesting part. certbot rotates the file on the host on its own
schedule; nginx has to notice. Use **variable certificate paths plus
`ssl_certificate_cache`**, which makes that automatic:

```nginx
# A map, not $ssl_server_name -- the value is constant, but referencing a variable
# is what selects nginx's re-reading code path.
map $host $frigate_cert { default /srv/frigate-certs/fullchain.pem; }
map $host $frigate_key  { default /srv/frigate-certs/privkey.pem; }

server {
    listen 8971 ssl;
    ssl_certificate     $frigate_cert;
    ssl_certificate_key $frigate_key;
    ssl_certificate_cache max=2 valid=1h;
    ...
}
```

With a literal path, nginx loads the certificate once at startup and a renewed file is
ignored until reload. With a variable path it loads per handshake, and
`ssl_certificate_cache` (nginx 1.27.4+) bounds that cost -- `valid=` is documented as
the time after which cached certificates "will be reloaded or revalidated", so a
rotated file is picked up within the TTL -- no `nginx -s reload` and no
`docker kill -s HUP` reaching from the host into the container runtime. That is worth
more here than the microseconds it costs, given `max=2`.

**A certbot `--deploy-hook` is required, because nginx runs nonroot.** The variable-path
approach loads the key at handshake time in a *worker* process, not at startup in the
root master, so the worker must be able to read it -- and certbot writes `privkey.pem`
0600 root. The hook stages a copy the nginx uid can read:

```bash
# certbot --deploy-hook, on the host. 65532 is the distroless nonroot uid/gid.
install -D -m 0640 -g 65532 \
  /etc/letsencrypt/live/noyan.example/fullchain.pem /srv/frigate-certs/fullchain.pem
install -D -m 0640 -g 65532 \
  /etc/letsencrypt/live/noyan.example/privkey.pem   /srv/frigate-certs/privkey.pem
```

Mount `/srv/frigate-certs` read-only and point the maps at it. This is strictly better
than mounting `/etc/letsencrypt` directly, for two reasons beyond permissions: the
container never sees the account key or any other host certificate, and the copies are
real files rather than the symlinks in `live/` that point into `../../archive/` and
would dangle unless the whole tree were mounted.

The hook only fires on renewal, so run it once by hand at install time to stage the
first copy. `valid=1h` is the detection window, not a security parameter -- certbot
renews at 30 days remaining, so an hour of staleness is immaterial.

The nginx image therefore ships one base config plus one TLS overlay, included from a
path that is empty by default -- so plain HTTP needs no configuration and Kubernetes
picks it up by doing nothing. The base config is byte-identical between the two
deployments.

## Architecture: amd64-only

Recommend **amd64-only**, and revisit only if whiterock actually becomes a deployment
target rather than a machine that happens to be arm64.

The cost is not evenly spread. go2rtc, sqlite-vec and jellyfin-ffmpeg all publish
arm64 assets, and `~/dockers/nginx/Dockerfile` already cross-compiles natively. The
expensive one is ours: `build_ncnn_wheel.sh` does a from-source CMake build of ncnn
with `NCNN_VULKAN=ON` and no cross-compilation setup, so arm64 means either a native
arm64 builder or QEMU emulation for the longest stage in the build. Multi-arch also
doubles the validation surface -- `scripts/validate_vulkan.sh` and the soak would both
need an arm64 run to mean anything, and the Vulkan/RADV behaviour this project exists
to pin down is AMD-discrete-GPU behaviour that whiterock does not have.

## Constraints carried over from the py313 image

These are ours to maintain regardless of packaging:

- **norfair pins `numpy<2.0.0`** and 2.3.0 is its last release. The pin is metadata
  only -- norfair and filterpy are pure Python and verified working on numpy 2.5.1 --
  so it is bypassed with `--no-deps`.
- **`tflite_runtime` has cp311 wheels only.** Shimmed onto `ai-edge-litert`, which
  exposes the same `Interpreter`/`load_delegate` API in 48MB rather than
  TensorFlow's 1.1GB. Required because `data_processing/real_time/bird.py` imports it
  at module level.
- **The embeddings stack cannot be dropped.** `frigate.app` imports it
  unconditionally via `api.classification`, which pulls `transformers`,
  `sherpa-onnx`, `librosa` and `regex`.
- **`UntrackedSharedMemory` is broken on 3.13.** cpython#82300 was fixed in 3.13 by
  adding a native `track=` keyword, which defeats Frigate's register() monkeypatch.
  See `docker/scripts/patch_untracked_shm.py`.

## Suggested first step

The `frigate` container itself -- no donor, no s6, single process. It proves the
from-source build before the nginx module build becomes worth the effort, and it is
the container that carries all four constraints above. Its entrypoint is short:
`rm -f /dev/shm/.frigate-is-stopping`, then `exec python3 -u -m frigate` from
`/opt/frigate`, with `LIBAVFORMAT_VERSION_MAJOR=61` set statically.

Doing this first also produces the go2rtc config renderer for free -- same image,
different command (`python3 /usr/local/go2rtc/create_config.py`), which is the
initContainer in Kubernetes and the `go2rtc-config` service under compose.

Validate it under compose first, against the donor image's go2rtc and nginx pulled in
as the other two services. That isolates one new container at a time and exercises the
`network_mode: "service:frigate"` wiring before either of the other two builds exists.

Run the **ncnn-from-Rust spike** in parallel, though. It is independent of all the
packaging work, it is a day rather than a week, and it is the assumption the entire
roadmap rests on -- better to know now than after three containers are built around it.
It is also the natural first commit in `corvette`: the repo starts as the thing that
proves the premise, before it is a UI or an NVR.

## Roadmap: Leptos UI, then a Rust NVR (`corvette`)

The longer arc is to stop being a Frigate derivative: a Rust/Leptos UI first, then a
Rust NVR behind it, with Frigate retired piece by piece rather than replaced at once.
**All of that Rust work lives in a separate repository, `corvette`.** This repo keeps
doing what it does now -- the ncnn/Vulkan detector plugin, and packaging it.

Two facts make that stageable rather than a rewrite-or-nothing bet.

**nginx is already the router.** `location /api/` proxies to `upstream frigate_api`,
a literal `127.0.0.1:5001`. Adding a Rust service to the pod means adding a second
upstream and moving routes across one at a time -- the strangler pattern falls out of
the architecture that already exists, with no new machinery and a per-route rollback.

**The hard part of the UI is already handled by components being kept.** Live video is
go2rtc (WebRTC/MSE on 1984) and recording playback is `nginx-vod` HLS on `/vod/`.
Frigate's React app embeds those; it does not implement them. A Leptos UI does the same.
What remains is CRUD over JSON and images -- events, review, config, stats, snapshots --
which is squarely what Leptos is good at.

### Sequence

| Stage | Scope | Repo | Frigate after |
| --- | --- | --- | --- |
| 0 | Leptos UI against Frigate's existing API | `corvette` | untouched |
| 1 | Backend trim -- overlay fork of `app.py`, `api/fastapi_app.py`, `api/classification.py` | this repo | -400MB, no embeddings |
| 2 | **ncnn-from-Rust spike** -- see below | `corvette` | untouched |
| 3 | Rust service in the pod, read-only routes first (stats, config, event list) | `corvette` | shares `/api` |
| 4 | Rust detection pipeline: frame ingest, motion, ncnn, tracking | `corvette` | detector retired |
| 5 | Rust recording, events, retention | `corvette` | Frigate retired |

Stage 0 is worth doing on its own merits even if nothing after it happens: it replaces
21MB of React, proves the trunk/wasm build in the nginx image, and it is what makes
stage 1 safe, since a UI you own is a contract you own. Note the dependency though --
stage 0 is where the Leptos bet is placed, and the case for Leptos over a TypeScript
framework rests on stages 3-5 actually landing. See "UI stack" below.

### Repository split

`corvette` owns the Rust: the NVR and the Leptos UI. This repo keeps the ncnn/Vulkan
detector plugin, the container builds and the deployment glue. The split is worth making
at the outset rather than extracting later, for three reasons:

- **Different languages, different lifecycles.** Nothing in `corvette` is pinned to
  `FRIGATE_VERSION`, and nothing here is pinned to a Rust toolchain.
- **This repo's reason to exist survives either outcome.** `docker/frigate/ncnn.py` plus
  the distroless packaging is useful whether or not the NVR ever ships. Keeping them
  separate means a stalled `corvette` does not strand the working thing.
- **The boundary is already container-shaped.** nginx routes between containers by
  upstream, so the strangler in stages 3-5 works across two repos with no extra
  machinery -- exactly the mechanism the split pod already provides.

The UI belongs in `corvette`, not here, because the argument for Leptos is shared serde
types with the Rust NVR -- splitting those across repos would forfeit it. That creates
the one real cross-repo dependency: **the nginx image needs `corvette`'s built UI
bundle.** Have `corvette` publish it as an OCI artifact and let the nginx stage `COPY
--from` it, pinned by a `CORVETTE_VERSION` ARG alongside `FRIGATE_VERSION` and
`NCNN_TAG`. That is the same shape as the donor image being retired here, with the
difference that this one is ours, small, and pinned by digest.

Expect the nginx container to migrate to `corvette` eventually -- once it is serving
`corvette`'s UI and `corvette`'s recordings, it has no reason to live here. Until stage
5 it still serves Frigate's `/vod/` and media, so it stays put.

### Do the ncnn spike early

Stage 2 is out of dependency order deliberately. Everything after it assumes ncnn is
usable from Rust with Vulkan, and that is the one assumption whose failure invalidates
the whole plan -- so it should be tested before stages 3-5 are committed to, not
discovered during stage 4.

The bindings exist: [`ncnn-bind`](https://rust-ncnn.github.io/ncnn_bind/) wraps ncnn's
C API, and shipping projects (`waifu2x-ncnn-vulkan-rs`,
[`realcugan-ncnn-vulkan-rs`](https://github.com/Snd-R/realcugan-ncnn-vulkan-rs)) run
ncnn+Vulkan from Rust today. The risk was that **ncnn's C API is narrower than the C++
API** the Python module uses.

Checked against `src/c_api.h` on 2026-08-03. Mapping every ncnn call in
`docker/frigate/ncnn.py`:

| `ncnn.py` uses | C API | |
| --- | --- | --- |
| `ncnn.Net()` | `ncnn_net_create` | present |
| `load_param` / `load_model` | `ncnn_net_load_param` / `ncnn_net_load_model` | present |
| `net.set_vulkan_device(i)` | `ncnn_net_set_vulkan_device` | present |
| `opt.use_vulkan_compute` | `ncnn_option_set_use_vulkan_compute` | present |
| `opt.use_fp16_packed/storage/arithmetic` | `ncnn_option_set_use_fp16_*` | all three present |
| `ncnn.Mat(numpy_array)` | `ncnn_mat_create_external_3d` | present |
| `create_extractor` / `input` / `extract` | `ncnn_extractor_*` | present |
| `ncnn.get_gpu_count()` | -- | **absent** |
| `ncnn.get_gpu_info(i).device_name()` | -- | **absent** |
| `ncnn.get_default_gpu_index()` | -- | **absent** |

**The inference path is fully covered; only GPU enumeration is missing.** Selection
works (`ncnn_net_set_vulkan_device`), discovery does not. That gap lands precisely on
the thing this project cares most about: `ncnn.py:105-109` selects the device explicitly
because `mesa-vulkan-drivers` installs lavapipe beside RADV, and a silent fall back onto
the software rasterizer looks like a working Vulkan path at a fraction of the speed.
Without enumeration a Rust port cannot log the device list, validate the index against
the device count, or default to ncnn's own choice.

Best fix: **extend the C API.** `build_ncnn_wheel.sh` already clones and builds ncnn
from source, so a small `c_api_ext` translation unit exposing `get_gpu_count`,
`get_gpu_info().device_name()` and `get_default_gpu_index()` compiles alongside it and
is bound like the rest. Alternatives are enumerating through `ash`/`vulkano` separately
(risks index order diverging from ncnn's) or requiring an explicit device in config and
dropping the validation (viable -- the profiles already set it -- but it removes a
guard that exists for a reason).

So the spike is narrower than it was: load the same YOLOv9 model, run the same input
through the C API with `use_vulkan_compute`, compare outputs and timing against
`scripts/bench_steady.py`, and prove the device-selection extension works on a host
where lavapipe is present.

### UI stack: Leptos, and why the video path is not the risk

The instinct is that WASM is a poor fit for a video-heavy UI. It is not, because
**WASM never touches video frames.** Frigate's bundle drives playback with `hls.js`,
`jsmpeg`, `RTCPeerConnection` and `MediaSource`/`SourceBuffer` -- all of which run in
the browser's native media pipeline. The framework creates a `<video>` element,
negotiates, and renders the chrome around it; decode happens in the browser's C++ stack
whether the app is React, Leptos or vanilla JS. The question is ecosystem access, not
throughput.

**Decision: drive MSE with fMP4 directly, and do not take a dependency on hls.js.**
`RTCPeerConnection` and `MediaSource` are browser APIs that `web-sys` binds natively, so
live (go2rtc WebRTC/MSE) needs no JS wrapper at all. Recordings are the only reason
Frigate needs `hls.js` -- Chrome and Firefox will not play HLS natively in `<video>`,
only Safari will. But `nginx.conf` already sets `vod_hls_container_format fmp4`, and the
`/stream/` location already advertises `application/dash+xml`, so the segments can be
fed to `MediaSource` from Rust directly. That removes the single largest JS dependency
the UI would otherwise have to wrap.

**The ecosystem gap is in the config editor and charts, not video.** In the current
bundle `ConfigEditor-*.js` is 3.0MB and its two Monaco workers add another 2.0MB --
about 5MB of 21MB is the YAML editor alone -- plus 524K of `react-apexcharts`. There is
no Rust Monaco, and Rust charting (plotters, charming) is thinner than the JS options.
This is the real cost to plan around. It may also evaporate: a new UI licenses a smaller
config schema, and a schema small enough to render as generated forms needs no YAML
editor at all.

**Leptos is justified by the Rust NVR, not by the UI.** The compounding win is shared
serde types across the boundary that is today pydantic on one side and hand-written
TypeScript on the other; fine-grained reactivity also suits a live dashboard of event
feed, camera state and stats over a websocket. Both are real, but the first is
contingent -- if stages 3-5 never land, most of the argument goes with them. Were this a
UI replacement alone, **SolidJS or Svelte** would be the better pick: the same
fine-grained reactivity model, native access to `hls.js` and the charting ecosystem,
much smaller than React. **Dioxus** is the alternative Rust option, worth revisiting only
if a desktop or mobile client ever matters.

Worth keeping in proportion: an NVR UI is lists, a grid of `<video>` elements and a
timeline scrubber. It is not a heavy reactive application, and the framework choice
should not become the project.

### Contracts a Rust NVR must honour

These are what the rest of the pod depends on, and they are small enough to write down:

- **`vod_mode mapped` + `vod_upstream_location /api`.** nginx-vod asks `/api` for a
  JSON mapping of a playback request to files on disk, then reads them itself. Whatever
  serves `/api` has to answer that, or recording playback stops working.
- **`/media/frigate` layout.** nginx serves `clips/`, `recordings/` and `exports/`
  directly with `root /media/frigate`, so the Rust writer keeps the layout or the nginx
  config changes with it.
- **`/dev/shm/go2rtc.yaml`.** A Rust NVR generates it directly and the whole
  initContainer dance in this document disappears -- one of the few places where
  replacing Frigate makes the deployment simpler rather than harder.
- **`/tmp/cache/birdseye`** only matters if birdseye is kept. It probably should not be.

### What is actually being rewritten

Not the detector. `docker/frigate/ncnn.py` is 181 lines and already exists; of the
4,587 LOC in `detectors/`, nearly all is other runtimes (OpenVINO, TensorRT, Hailo,
degirum, EdgeTPU) that this project never loads. The rewrite is the ~54k LOC that makes
Frigate an NVR rather than a detector, and it is worth being clear-eyed about which
parts are hard:

- **Frame ingest** is not hard -- Frigate spawns ffmpeg per camera with rawvideo to a
  pipe. Rust does the same; no libav binding required.
- **Tracking** is bounded -- norfair is Kalman + Hungarian assignment, and Rust has
  equivalents (`similari`) or it can be ported.
- **Config schema** is 3,202 LOC of pydantic. A serde equivalent is real work, but a
  new UI means a smaller schema is allowed.
- **Retention and storage management** is the genuinely risky one. It is subtle, it is
  where bugs destroy recordings rather than merely erroring, and it deserves to be last.

## Open questions

- Sizing for frigate's `/dev/shm` `emptyDir` / `shm_size`. Frigate computes a required
  size from camera count and resolution; take the number from a running instance rather
  than guessing.
- **How to restore GPU enumeration for the Rust port.** The C API covers the whole
  inference path but not `get_gpu_count`/`get_gpu_info`/`get_default_gpu_index`;
  recommendation is a small `c_api_ext` built alongside the vendored ncnn. Settle it in
  the spike -- it is the only unresolved part of the roadmap's core assumption. See
  "Do the ncnn spike early".
- How much of Frigate's config schema the Leptos UI should expose, and whether it needs
  a text editor at all. This now has a concrete stake at both ends: Monaco is ~5MB of
  the current 21MB bundle and has no Rust equivalent, and every field dropped is a field
  the Rust NVR does not have to reimplement in stage 5. Generated forms over a small
  serde schema would settle both. Worth deciding deliberately rather than by accretion.
- **Confirm 65532 everywhere.** The frigate base arrives at that uid, so the obvious
  move is to run nginx and go2rtc there too and be done. It is load-bearing in four
  places -- the staged certificate's group, ownership of `/run` and
  `/dev/shm/nginx_cache`, and read access to `/media/frigate` and `/tmp`, both written
  by frigate -- so the one thing that must not happen is the three containers picking
  different uids. Existing `/media/frigate` content is presently root-owned and will
  need chown'ing once.
- **The GPU library closure**, now the main maintenance surface: which Mesa, VA-API and
  Vulkan ICD files come across from the trixie builder, and how that is re-verified on
  a Mesa bump. Most likely of the remaining work to fail late and quietly, since a
  missing ICD degrades to software rather than erroring.
- **`RENDER_GID` becomes load-bearing for the first time.** Tested: as uid 65532 both
  `radeontop` and `vainfo` work against `renderD128` alone -- `card1` and the video gid
  are not needed, so `VIDEO_GID` can be dropped. But a *wrong* `RENDER_GID` has always
  been harmless because root bypasses the permission check via `CAP_DAC_OVERRIDE`;
  nonroot turns it into a hard failure on any host where `renderD128` is 0660 rather
  than 0666. The value is already per-host (`profiles/gfx906-vulkan.env` notes 992 on
  trixie vs 109 on bookworm), so this is a matter of confirming it per deployment, not
  of new machinery -- but it will bite on the first nonroot run if it is stale.
