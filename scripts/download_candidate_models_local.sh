#!/bin/sh
set -eu

root_dir=${1:-runtime/model-downloads}
mirror=${CANDIDATE_HF_BASE_URL:-https://hf-mirror.com}
connections=${CANDIDATE_DOWNLOAD_CONNECTIONS:-4}

if ! command -v aria2c >/dev/null 2>&1; then
  echo "缺少 aria2c，请先安装 aria2" >&2
  exit 1
fi

mkdir -p "$root_dir/flux2"
root_dir=$(cd "$root_dir" && pwd)
rm -f "$root_dir/CandidateModels.ready"

# 方法说明：计算指定模型文件的 SHA-256 摘要。
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# 方法说明：校验模型权重的大小和摘要。
verify_weight() {
  relative_path=$1
  expected_size=$2
  expected_hash=$3
  path="$root_dir/$relative_path"
  test -f "$path" || return 1
  actual_size=$(wc -c <"$path" | tr -d ' ')
  test "$actual_size" = "$expected_size" || return 1
  test "$(hash_file "$path")" = "$expected_hash"
}

# 方法说明：断点下载并校验一个 FLUX.2 模型权重。
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

  aria2c \
    --continue=true \
    --allow-overwrite=true \
    --auto-file-renaming=false \
    --file-allocation=none \
    --max-concurrent-downloads=1 \
    --max-connection-per-server="$connections" \
    --min-split-size=8M \
    --split="$connections" \
    --max-tries=0 \
    --retry-wait=5 \
    --timeout=60 \
    --console-log-level=warn \
    --summary-interval=10 \
    --dir="$target_dir" \
    --out="$name" \
    "$mirror/$repository_path"

  if ! verify_weight "$relative_path" "$expected_size" "$expected_hash"; then
    echo "$relative_path 大小或 SHA-256 校验失败" >&2
    return 1
  fi
  echo "$relative_path 下载并校验完成"
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

# 方法说明：终止尚未完成的并行下载任务。
cleanup() {
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
}
trap cleanup INT TERM EXIT
for pid in $pids; do
  wait "$pid"
done
trap - INT TERM EXIT

manifest="$root_dir/CandidateModels.sha256"
(
  cd "$root_dir"
  find flux2 -type f \
    ! -name '*.aria2' \
    ! -name '*.log' \
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
echo "FLUX.2 Klein 候选模型下载并校验完成：$root_dir"
