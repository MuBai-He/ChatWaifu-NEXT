#!/usr/bin/env bash
set -euo pipefail

task_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
task_framework_dir="$task_repo_root/vendor/live2d/CubismWebFramework"
task_expected_tag="5-r.5"
task_expected_commit="198a3769c26ca3d7b600e932590433badd392edd"

if [[ -d "$task_framework_dir/.git" ]]; then
  task_current_commit=$(git -C "$task_framework_dir" rev-parse HEAD)
  if [[ "$task_current_commit" != "$task_expected_commit" ]]; then
    echo "Live2D Framework exists at an unexpected commit: $task_current_commit" >&2
    echo "Expected $task_expected_commit ($task_expected_tag). Move it aside and rerun." >&2
    exit 1
  fi
  echo "Live2D Cubism Web Framework $task_expected_tag is already present."
  exit 0
fi

if [[ -e "$task_framework_dir" ]]; then
  echo "$task_framework_dir exists but is not a Git checkout. Move it aside and rerun." >&2
  exit 1
fi

git clone --depth 1 --branch "$task_expected_tag" \
  https://github.com/Live2D/CubismWebFramework.git "$task_framework_dir"

task_current_commit=$(git -C "$task_framework_dir" rev-parse HEAD)
if [[ "$task_current_commit" != "$task_expected_commit" ]]; then
  echo "Cloned commit $task_current_commit does not match pinned commit $task_expected_commit." >&2
  exit 1
fi
echo "Installed Live2D Cubism Web Framework $task_expected_tag."
