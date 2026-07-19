# Safety and limitations

The Vulkan path is experimental. A successful image build does not establish
that a host GPU is visible through RADV or that detection is accurate.

- Run the smoke target before relying on Frigate. With fp16 disabled, its
  GPU/CPU maximum absolute-error gate is `< 1e-2`.
- Polaris-class GPUs do not have fast fp16 arithmetic. `NCNN_FP16` defaults to
  `0`; enable it only after measuring accuracy and performance on the host.
- Containers require `/dev/dri` plus matching `video` and `render` group IDs.
  They intentionally do not expose `/dev/kfd`.
- The ncnn detector expects a YOLO ncnn `.param`/`.bin` pair exported at the
  configured dimensions. Use `scripts/export_ncnn_model.sh` for YOLOv9-t.
