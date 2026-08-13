#!/bin/sh
set -eu

source_dir=${1:-runtime/model-downloads/magiv2}
remote=${MAGIV2_REMOTE:-holopix@192.168.38.226}
remote_dir=${MAGIV2_REMOTE_DIR:-/data1/models/ComfyUI/models/magiv2}

source_dir=$(cd "$source_dir" && pwd)
test -f "$source_dir/MAGIv2.ready"
cd "$source_dir"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check MAGIv2.sha256
else
  shasum -a 256 -c MAGIv2.sha256
fi

ssh "$remote" "mkdir -p '$remote_dir' && rm -f '$remote_dir/MAGIv2.ready'"
for name in \
  pytorch_model.bin \
  config.json \
  configuration_magiv2.py \
  modelling_magiv2.py \
  processing_magiv2.py \
  utils.py \
  REVISION; do
  expected=$(awk -v file="$name" '$2 == file { print $1 }' MAGIv2.sha256)
  remote_sha256=$(
    ssh "$remote" \
      "test -f '$remote_dir/$name' && sha256sum '$remote_dir/$name' | cut -d' ' -f1" \
      || true
  )
  if [ "$remote_sha256" = "$expected" ]; then
    echo "$name 远端哈希一致，跳过上传"
    continue
  fi
  rsync --partial --progress \
    "$source_dir/$name" \
    "$remote:$remote_dir/$name.uploading"
  ssh "$remote" "mv '$remote_dir/$name.uploading' '$remote_dir/$name'"
done

rsync MAGIv2.sha256 "$remote:$remote_dir/MAGIv2.sha256.uploading"
ssh "$remote" "set -eu
mv '$remote_dir/MAGIv2.sha256.uploading' '$remote_dir/MAGIv2.sha256'
cd '$remote_dir'
sha256sum --check MAGIv2.sha256
touch MAGIv2.ready"

echo "MAGIv2 已上传并完成远端校验：$remote:$remote_dir"
