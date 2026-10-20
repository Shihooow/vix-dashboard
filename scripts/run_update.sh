#!/usr/bin/env bash
# 毎日の数値更新(取得 → 再生成 → mainへ公開)を1本にまとめたスクリプト(2026-10-20)。
# GitHub Actions と、Claudeのクラウドのスケジュールタスク(毎朝6:45)の両方から同じものを呼ぶ。
# 何度実行しても安全: 数値が変わらなければコミットしない。pushが競合したら最新のmainに合わせて作り直す。
#
#   bash scripts/run_update.sh          # 取得・再生成・コミット・push
#   bash scripts/run_update.sh --no-push   # 取得・再生成だけ(手元での確認用)
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Asia/Tokyo

FILES="index.html dashboard.html log.csv signals.json markets.json"

generate() {
  python3 scripts/fetch_signals.py
  python3 scripts/update_dashboard.py --from-signals
  cp dashboard.html index.html
  grep -q "地合いシグナル" dashboard.html
  grep -q "今日のアクション" dashboard.html
}

generate
[ "${1:-}" = "--no-push" ] && exit 0

git config user.name "VIX Dashboard Bot"
git config user.email "vix-dashboard-bot@users.noreply.github.com"

for i in 1 2 3; do
  git add -A $FILES
  if git diff --cached --quiet; then
    echo "no changes"; exit 0
  fi
  git commit -q -m "update $(date +%F) $(date +%H:%M)"
  if git push -q origin HEAD:main; then
    echo "pushed"
    # GITHUB_TOKEN でのpushはPagesの再ビルドを起こさないことがあるので明示的に依頼する(Actions上のみ)
    if [ -n "${GITHUB_REPOSITORY:-}" ]; then
      gh api -X POST "repos/${GITHUB_REPOSITORY}/pages/builds" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
  # 別の実行が先にpushしていた: 最新のmainに合わせて作り直す(生成物は毎回データから作り直せる)
  echo "push競合のため最新のmainで作り直し($i)"
  git fetch -q origin main
  git reset -q --hard origin/main
  sleep 5
  generate
done
echo "push失敗"; exit 1
