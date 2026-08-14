#!/bin/sh
set -eu

root_dir=${1:-runtime/model-downloads}
mirror=${CANDIDATE_HF_BASE_URL:-https://hf-mirror.com}
connections=${CANDIDATE_DOWNLOAD_CONNECTIONS:-4}
cobra_revision=ee2fd07574c9ea8582a7045620b9901998b42bce
pixart_revision=b89adadeccd9ead2adcb9fa2825d3fabec48d404

if ! command -v aria2c >/dev/null 2>&1; then
  echo "缺少 aria2c，请先安装 aria2" >&2
  exit 1
fi

mkdir -p "$root_dir/flux2" "$root_dir/cobra"
root_dir=$(cd "$root_dir" && pwd)
rm -f "$root_dir/CandidateModels.ready"

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

verify_weight() {
  relative_path=$1
  expected_size=$2
  expected_hash=$3
  path="$root_dir/$relative_path"
  test -f "$path" || return 1
  actual_size=$(wc -c <"$path" | tr -d ' ')
  test "$actual_size" = "$expected_size" || return 1
  actual_hash=$(hash_file "$path")
  test "$actual_hash" = "$expected_hash"
}

download_weight() {
  relative_path=$1
  repository_path=$2
  expected_size=$3
  expected_hash=$4
  target_dir=$(dirname "$root_dir/$relative_path")
  name=$(basename "$relative_path")
  mkdir -p "$target_dir"

  if verify_weight "$relative_path" "$expected_size" "$expected_hash"; then
    echo "$relative_path 已存在且校验通过"
    return
  fi

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
      "$mirror/$repository_path" \
      >"$target_dir/$name.log" 2>&1; do
    attempt=$((attempt + 1))
    delay=$((attempt * 15))
    if [ "$delay" -gt 120 ]; then
      delay=120
    fi
    echo "$relative_path 下载中断，${delay} 秒后续传" >&2
    sleep "$delay"
  done

  if ! verify_weight "$relative_path" "$expected_size" "$expected_hash"; then
    echo "$relative_path 大小或 SHA-256 校验失败" >&2
    return 1
  fi
  echo "$relative_path 下载并校验完成"
}

download_small() {
  relative_path=$1
  repository_path=$2
  target="$root_dir/$relative_path"
  temporary="$target.downloading"
  mkdir -p "$(dirname "$target")"
  curl -fL --retry 8 --retry-all-errors --connect-timeout 20 \
    "$mirror/$repository_path" -o "$temporary"
  test -s "$temporary"
  mv "$temporary" "$target"
}

pids=""
download_weight flux2/flux-2-klein-4b-fp8.safetensors \
  black-forest-labs/FLUX.2-klein-4b-fp8/resolve/main/flux-2-klein-4b-fp8.safetensors \
  4070624520 97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6 &
pids="$pids $!"
download_weight flux2/qwen_3_4b.safetensors \
  Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors \
  8044982048 6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a &
pids="$pids $!"

download_weight cobra/JunhaoZhuang-Cobra/LE/erika.pth \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/LE/erika.pth \
  172789563 badbd6baf013cefbd98993307b02cc14a26c770d067416e4fdecc8720b88feeb &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/image_encoder/pytorch_model.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/image_encoder/pytorch_model.bin \
  2528481905 3d3ec1e66737f77a4f3bc2df3c52eacefc69ce7825e2784183b1d4e9877d9193 &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/line_GSRP/MultiResNetModel.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/line_GSRP/MultiResNetModel.bin \
  340148769 056b805a02d775dd18920851307f8bcc8fdcfd3f09197d945cb682ad9a2ed7dd &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/line_ckpt/controlnet.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/line_ckpt/controlnet.bin \
  503963680 b4cc47bb796ffdbb7cbcde7ebd38b4b35db1f3e61e4b7b4b71257229876fa27c &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/line_ckpt/transformer_lora_pos.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/line_ckpt/transformer_lora_pos.bin \
  221169512 1fdd6edc0b1e3643690e1ab38d2d4e94f72eb7671d073b346ea7472ca884b9f6 &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/shadow_GSRP/MultiResNetModel.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/shadow_GSRP/MultiResNetModel.bin \
  340148769 2b1c2cf7c53e627419b4741455fc69ed51922914dda3e54ed0ba4a9e2883d3b3 &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/shadow_ckpt/controlnet.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/shadow_ckpt/controlnet.bin \
  503963680 fd42fb398eab730a3d54d2f350047a47816fce12b6daf9de62f50ff6550db905 &
pids="$pids $!"
download_weight cobra/JunhaoZhuang-Cobra/shadow_ckpt/transformer_lora_pos.bin \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/shadow_ckpt/transformer_lora_pos.bin \
  221169512 62bbec6b7ce1e73f1dafd241843e4204aadeeea9f8c336512d0a5cd83e1c9c83 &
pids="$pids $!"

download_weight cobra/PixArt-XL-2-1024-MS/transformer/diffusion_pytorch_model.safetensors \
  PixArt-alpha/PixArt-XL-2-1024-MS/resolve/$pixart_revision/transformer/diffusion_pytorch_model.safetensors \
  2447431856 809a92d52a4a228f381a4b4f4b76051294b73285fb0cbb02f0ad24f9372217a8 &
pids="$pids $!"
download_weight cobra/PixArt-XL-2-1024-MS/vae/diffusion_pytorch_model.safetensors \
  PixArt-alpha/PixArt-XL-2-1024-MS/resolve/$pixart_revision/vae/diffusion_pytorch_model.safetensors \
  334643268 703abdcd7c389316b5128faa9b750a530ea1680b453170b27afebac5e4db30c4 &
pids="$pids $!"
cleanup() {
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
}
trap cleanup INT TERM EXIT
for pid in $pids; do
  wait "$pid"
done
trap - INT TERM EXIT

download_small cobra/JunhaoZhuang-Cobra/image_encoder/config.json \
  JunhaoZhuang/Cobra/resolve/$cobra_revision/image_encoder/config.json
for path in \
  model_index.json \
  scheduler/scheduler_config.json \
  text_encoder/config.json \
  tokenizer/added_tokens.json \
  tokenizer/special_tokens_map.json \
  tokenizer/spiece.model \
  tokenizer/tokenizer_config.json \
  transformer/config.json \
  vae/config.json; do
  download_small "cobra/PixArt-XL-2-1024-MS/$path" \
    "PixArt-alpha/PixArt-XL-2-1024-MS/resolve/$pixart_revision/$path"
done

manifest="$root_dir/CandidateModels.sha256"
(
  cd "$root_dir"
  find flux2 cobra -type f \
    ! -name '*.aria2' \
    ! -name '*.log' \
    ! -name '*.downloading' \
    ! -path 'cobra/PixArt-XL-2-1024-MS/text_encoder/model-*.safetensors' \
    ! -path 'cobra/PixArt-XL-2-1024-MS/text_encoder/model.safetensors.index.json' \
    | LC_ALL=C sort \
    | while IFS= read -r path; do
        if command -v sha256sum >/dev/null 2>&1; then
          sha256sum "$path"
        else
          shasum -a 256 "$path"
        fi
      done
) >"$manifest"

touch "$root_dir/CandidateModels.ready"
echo "Cobra 与 FLUX.2 Klein 候选模型下载并校验完成：$root_dir"
