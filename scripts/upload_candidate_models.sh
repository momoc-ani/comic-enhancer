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
ssh -n "$remote" "mkdir -p '$remote_root/diffusion_models' '$remote_root/text_encoders' '$remote_root/cobra' && rm -f '$remote_ready'"

# 方法说明：上传候选模型文件到远端主机。
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

cobra_list=$(mktemp)
trap 'rm -f "$cobra_list"' EXIT INT TERM
find cobra -type f \
  ! -name '*.aria2' \
  ! -name '*.log' \
  ! -name '*.downloading' \
  ! -path 'cobra/PixArt-XL-2-1024-MS/text_encoder/model-*.safetensors' \
  ! -path 'cobra/PixArt-XL-2-1024-MS/text_encoder/model.safetensors.index.json' \
  | LC_ALL=C sort >"$cobra_list"

expected_cobra_files=$(wc -l <"$cobra_list" | tr -d ' ')
test "$expected_cobra_files" -ge 20
processed_cobra_files=0
while IFS= read -r path; do
  upload "$path" "$path"
  processed_cobra_files=$((processed_cobra_files + 1))
done <"$cobra_list"
test "$processed_cobra_files" = "$expected_cobra_files"
rm -f "$cobra_list"
trap - EXIT INT TERM

ssh -n "$remote" "set -eu
reused_t5='$remote_root/clip/t5xxl_fp16.safetensors'
expected_t5_size='9787841024'
expected_t5_hash='6e480b09fae049a72d2a8c5fbccb8d3e92febeb233bbe9dfe7256958a9167635'
test -f \"\$reused_t5\"
actual_t5_size=\$(wc -c < \"\$reused_t5\" | tr -d ' ')
test \"\$actual_t5_size\" = \"\$expected_t5_size\"
actual_t5_hash=\$(sha256sum \"\$reused_t5\" | cut -d' ' -f1)
test \"\$actual_t5_hash\" = \"\$expected_t5_hash\"
text_encoder_dir='$remote_root/cobra/PixArt-XL-2-1024-MS/text_encoder'
mkdir -p \"\$text_encoder_dir\"
rm -f \"\$text_encoder_dir/model.safetensors.index.json\"
ln -sfn '../../../clip/t5xxl_fp16.safetensors' \"\$text_encoder_dir/model.safetensors\"
test -s \"\$text_encoder_dir/model.safetensors\"
test -s '$remote_root/vae/flux2-vae.safetensors'
touch '$remote_ready'"

echo "候选模型已上传并完成远端 SHA-256 校验：$remote:$remote_root"
