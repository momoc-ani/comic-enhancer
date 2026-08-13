#!/bin/sh
set -eu

target_dir=${1:-runtime/model-downloads/MangaNinjia}
base_url=${MANGANINJA_HF_BASE_URL:-https://hf-mirror.com}
connections=${MANGANINJA_DOWNLOAD_CONNECTIONS:-4}

if ! command -v aria2c >/dev/null 2>&1; then
  echo "缺少 aria2c，请先安装 aria2" >&2
  exit 1
fi

mkdir -p "$target_dir"
target_dir=$(cd "$target_dir" && pwd)

download() {
  name=$1
  repository=$2
  attempt=0
  while ! aria2c \
      --continue=true \
      --allow-overwrite=true \
      --auto-file-renaming=false \
      --file-allocation=none \
      --max-concurrent-downloads=1 \
      --max-connection-per-server="$connections" \
      --min-split-size=8M \
      --split="$connections" \
      --max-tries=5 \
      --retry-wait=5 \
      --timeout=60 \
      --console-log-level=warn \
      --summary-interval=10 \
      --dir="$target_dir" \
      --out="$name" \
      "$base_url/$repository/resolve/main/$name" \
      >"$target_dir/$name.log" 2>&1; do
    attempt=$((attempt + 1))
    delay=$((attempt * 15))
    if [ "$delay" -gt 120 ]; then
      delay=120
    fi
    echo "$name 下载受限或中断，${delay} 秒后从已有分片续传" >&2
    sleep "$delay"
  done
}

download denoising_unet.pth Johanan0528/MangaNinjia &
denoising_pid=$!
download reference_unet.pth Johanan0528/MangaNinjia &
reference_pid=$!
download controlnet.pth Johanan0528/MangaNinjia &
controlnet_pid=$!
download point_net.pth Johanan0528/MangaNinjia &
point_net_pid=$!
download sk_model.pth lllyasviel/Annotators &
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

cat >"$target_dir/MangaNinjia.sha256" <<'EOF'
819f21a628c4504aafe0e43aad4fd8db7ee49c20e814b86dda875695ec4c2946  denoising_unet.pth
d2ba65b59f874c242fafd6abffbc2a29955b44208018967b7924255384e8c367  reference_unet.pth
233ea18732ed9639cbf5e6586ed4095b7bc4e61f5116ca16d6557a7b536b802a  point_net.pth
08df173edd02005887dae404fddc5b3f7e64e99b50fac66193597a4870eaaea3  controlnet.pth
c686ced2a666b4850b4bb6ccf0748031c3eda9f822de73a34b8979970d90f0c6  sk_model.pth
EOF

cd "$target_dir"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check MangaNinjia.sha256
else
  shasum -a 256 -c MangaNinjia.sha256
fi
touch MangaNinjia.ready
echo "MangaNinja 五项权重下载并校验完成：$target_dir"
