#!/bin/sh
set -eu

dir=/data1/models/ComfyUI/models/MangaNinjia
log=/tmp/comic-manganinja-parallel.log
base_url=${MANGANINJA_HF_BASE_URL:-https://huggingface.co}
cd "$dir"

python3 /tmp/download_hf_parallel.py \
  "$base_url/Johanan0528/MangaNinjia/resolve/main/denoising_unet.pth" \
  denoising_unet.pth 3438370164 --workers 16 --retries 5 &
denoising_pid=$!
python3 /tmp/download_hf_parallel.py \
  "$base_url/Johanan0528/MangaNinjia/resolve/main/reference_unet.pth" \
  reference_unet.pth 3438320160 --workers 16 --retries 5 &
reference_pid=$!
python3 /tmp/download_hf_parallel.py \
  "$base_url/Johanan0528/MangaNinjia/resolve/main/controlnet.pth" \
  controlnet.pth 1445255634 --workers 12 --retries 5 &
controlnet_pid=$!
python3 /tmp/download_hf_parallel.py \
  "$base_url/Johanan0528/MangaNinjia/resolve/main/point_net.pth" \
  point_net.pth 103252178 --workers 4 --retries 5 &
point_net_pid=$!
python3 /tmp/download_hf_parallel.py \
  "$base_url/lllyasviel/Annotators/resolve/main/sk_model.pth" \
  sk_model.pth 17173511 --workers 2 --retries 5 &
sk_model_pid=$!

cleanup() {
  kill \
    "$denoising_pid" \
    "$reference_pid" \
    "$controlnet_pid" \
    "$point_net_pid" \
    "$sk_model_pid" \
    2>/dev/null || true
}
trap cleanup INT TERM EXIT
wait "$denoising_pid"
wait "$reference_pid"
wait "$controlnet_pid"
wait "$point_net_pid"
wait "$sk_model_pid"
trap - INT TERM EXIT

printf '%s  %s\n' \
  819f21a628c4504aafe0e43aad4fd8db7ee49c20e814b86dda875695ec4c2946 denoising_unet.pth \
  d2ba65b59f874c242fafd6abffbc2a29955b44208018967b7924255384e8c367 reference_unet.pth \
  233ea18732ed9639cbf5e6586ed4095b7bc4e61f5116ca16d6557a7b536b802a point_net.pth \
  08df173edd02005887dae404fddc5b3f7e64e99b50fac66193597a4870eaaea3 controlnet.pth \
  c686ced2a666b4850b4bb6ccf0748031c3eda9f822de73a34b8979970d90f0c6 sk_model.pth \
  > MangaNinjia.sha256
sha256sum --check MangaNinjia.sha256
touch MangaNinjia.ready
