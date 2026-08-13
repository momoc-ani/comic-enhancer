#!/bin/sh
set -eu

source_dir=${1:-runtime/model-downloads/MangaNinjia}
remote=${MANGANINJA_REMOTE:-holopix@192.168.38.226}
remote_dir=${MANGANINJA_REMOTE_DIR:-/data1/models/ComfyUI/models/MangaNinjia}

source_dir=$(cd "$source_dir" && pwd)
test -f "$source_dir/MangaNinjia.ready"

cd "$source_dir"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check MangaNinjia.sha256
else
  shasum -a 256 -c MangaNinjia.sha256
fi

ssh "$remote" "mkdir -p '$remote_dir' && rm -f '$remote_dir/MangaNinjia.ready'"
for name in denoising_unet.pth reference_unet.pth point_net.pth controlnet.pth sk_model.pth; do
  expected=$(awk -v file="$name" '$2 == file { print $1 }' MangaNinjia.sha256)
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

ssh "$remote" "cat >'$remote_dir/MangaNinjia.sha256' <<'EOF'
819f21a628c4504aafe0e43aad4fd8db7ee49c20e814b86dda875695ec4c2946  denoising_unet.pth
d2ba65b59f874c242fafd6abffbc2a29955b44208018967b7924255384e8c367  reference_unet.pth
233ea18732ed9639cbf5e6586ed4095b7bc4e61f5116ca16d6557a7b536b802a  point_net.pth
08df173edd02005887dae404fddc5b3f7e64e99b50fac66193597a4870eaaea3  controlnet.pth
c686ced2a666b4850b4bb6ccf0748031c3eda9f822de73a34b8979970d90f0c6  sk_model.pth
EOF
cd '$remote_dir'
sha256sum --check MangaNinjia.sha256
touch MangaNinjia.ready"

echo "MangaNinja 权重已上传并完成远端校验：$remote:$remote_dir"
