#!/bin/sh
set -eu

target_dir=${1:-runtime/model-downloads/magiv2}
base_url=${MAGIV2_HF_BASE_URL:-https://hf-mirror.com}
revision=${MAGIV2_REVISION:-fbc890fec52977142e8ee00bfe26e9458b65517c}
connections=${MAGIV2_DOWNLOAD_CONNECTIONS:-4}

if ! command -v aria2c >/dev/null 2>&1; then
  echo "缺少 aria2c，请先安装 aria2" >&2
  exit 1
fi

mkdir -p "$target_dir"
target_dir=$(cd "$target_dir" && pwd)
rm -f "$target_dir/MAGIv2.ready"

download() {
  name=$1
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
      "$base_url/ragavsachdeva/magiv2/resolve/$revision/$name" \
      >"$target_dir/$name.log" 2>&1; do
    echo "$name 下载受限或中断，15 秒后从已有分片续传" >&2
    sleep 15
  done
}

download pytorch_model.bin &
weight_pid=$!
download config.json &
config_pid=$!
download configuration_magiv2.py &
configuration_pid=$!
download modelling_magiv2.py &
model_pid=$!
download processing_magiv2.py &
processor_pid=$!
download utils.py &
utils_pid=$!

cleanup() {
  kill \
    "$weight_pid" \
    "$config_pid" \
    "$configuration_pid" \
    "$model_pid" \
    "$processor_pid" \
    "$utils_pid" \
    2>/dev/null || true
}
trap cleanup INT TERM EXIT
wait "$weight_pid"
wait "$config_pid"
wait "$configuration_pid"
wait "$model_pid"
wait "$processor_pid"
wait "$utils_pid"
trap - INT TERM EXIT

cat >"$target_dir/MAGIv2.sha256" <<'EOF'
56392403204d3a4cca38694a3a260a6929d741869d802d6b14de35b4eab4c4b8  pytorch_model.bin
d55b3b0b6c16d61c97c4afd79b0d0e9b2371a4a3442f7dbef2ff838cb089e19f  config.json
28e65be99f4287dea300a49ae65ced263054b756113d1f37baf8f1b0fd94e72b  configuration_magiv2.py
1188ebcb84985fe812e222e51bf0889c586bdbe6f2913f7cbefaf43ffe74e6de  modelling_magiv2.py
2ff8e760e65848ee5d02eaca84ce24381ced97e4cbcf26d2f838d05362a946a8  processing_magiv2.py
ea9d9182ee2d02f8dba1cca634dfc3e3528bab50de9e9efa286822ac5ee3195f  utils.py
9b4504cf38d6dbdac6f3f0aedbec2dd995ed953ee65efa79da290c23a5bc9c25  REVISION
EOF

cd "$target_dir"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check MAGIv2.sha256
else
  shasum -a 256 -c MAGIv2.sha256
fi
printf '%s\n' "$revision" > REVISION
touch MAGIv2.ready
echo "MAGIv2 固定版本已从国内镜像下载并校验：$target_dir"
