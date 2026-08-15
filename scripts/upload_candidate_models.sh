#!/bin/sh
# shellcheck disable=SC2029
set -eu

source_root=${1:-runtime/model-downloads}
remote=${CANDIDATE_REMOTE:-holopix@192.168.38.226}
remote_root=${CANDIDATE_REMOTE_MODELS_ROOT:-/data1/models/ComfyUI/models}

source_root=$(cd "$source_root" && pwd)
test -f "$source_root/CandidateModels.ready"
test -f "$source_root/CandidateModels.sha256"

cd "$source_root"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check CandidateModels.sha256
else
  shasum -a 256 -c CandidateModels.sha256
fi

remote_ready="$remote_root/candidate-models.ready"
ssh -n "$remote" "mkdir -p '$remote_root/diffusion_models' '$remote_root/text_encoders' && rm -f '$remote_ready'"

# 方法说明：上传并校验一个 FLUX.2 候选模型文件。
upload() {
  local_path=$1
  remote_path=$2
  expected=$(awk -v file="$local_path" '$2 == file { print $1 }' CandidateModels.sha256)
  test -n "$expected"
  remote_hash=$(
    ssh -n "$remote" \
      "test -f '$remote_root/$remote_path' && sha256sum '$remote_root/$remote_path' | cut -d' ' -f1" \
      || true
  )
  if [ "$remote_hash" = "$expected" ]; then
    echo "$remote_path 远端哈希一致，跳过上传"
    return
  fi

  ssh -n "$remote" "mkdir -p '$(dirname "$remote_root/$remote_path")'"
  rsync --partial --progress \
    "$source_root/$local_path" \
    "$remote:$remote_root/$remote_path.uploading"
  ssh -n "$remote" "set -eu
actual=\$(sha256sum '$remote_root/$remote_path.uploading' | cut -d' ' -f1)
test \"\$actual\" = '$expected'
mv '$remote_root/$remote_path.uploading' '$remote_root/$remote_path'"
}

upload flux2/flux-2-klein-4b-fp8.safetensors \
  diffusion_models/flux-2-klein-4b-fp8.safetensors
upload flux2/qwen_3_4b.safetensors text_encoders/qwen_3_4b.safetensors

ssh -n "$remote" "set -eu
test -s '$remote_root/vae/flux2-vae.safetensors'
touch '$remote_ready'"

echo "FLUX.2 候选模型已上传并完成远端 SHA-256 校验：$remote:$remote_root"
