# Supported matrix

| Profile | Example family | Driver/runtime | Inference engine | Status |
|---|---|---|---|---|
| `gfx803-vulkan` | Polaris / RX 500 style cards | Mesa RADV + Vulkan | ncnn | experimental |

## Support levels

- Level 1: Images build successfully.
- Level 2: ncnn enumerates the Vulkan GPU.
- Level 3: GPU/CPU model parity is below the configured threshold.
- Level 4: Frigate detection works under sustained use.

`gfx803-vulkan` has only Level 1 evidence until the hardware validation results
in [Vulkan validation notes](vulkan-notes.md) are recorded.
