#!/usr/bin/env bash
# One GitHub Actions slice of the PAPER stock run: restore state from the `data` branch, run supervisor+bot
# for up to RUN_SECONDS (or until the bot says the market is closed), save state back to `data` every
# SAVE_EVERY seconds and at the end. No secrets, no broker.
set -uo pipefail
cd "$(dirname "$0")/.."
RUN_SECONDS=${RUN_SECONDS:-20400}     # 5h40m (job limit is 6h)
SAVE_EVERY=${SAVE_EVERY:-600}         # 10 min
ST="${RUNNER_TEMP:-/tmp}/state"

git config user.name "stock-paper-bot"
git config user.email "stock-paper-bot@users.noreply.github.com"

mkdir -p data
if git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
  git fetch -q origin data
  git worktree add -f -B data "$ST" origin/data >/dev/null
  cp -a "$ST/data/." data/ 2>/dev/null || true
  echo "restored state: $(ls data | tr '\n' ' ')"
else
  git worktree add --orphan -b data "$ST" >/dev/null 2>&1 || { mkdir -p "$ST"; git -C "$ST" init -q; }
  echo "no data branch yet; starting fresh"
fi
rm -f data/heartbeat.json data/*.pid .slice_stop

save() {
  mkdir -p "$ST/data"
  cp -a data/trades_*.csv data/state.json data/heartbeat.json "$ST/data/" 2>/dev/null || true
  tail -n 600 data/bot.log > "$ST/data/bot_tail.log" 2>/dev/null || true
  ( cd "$ST" && git add -A data && git commit -qm "state $(TZ=America/Los_Angeles date '+%Y-%m-%d %H:%M PT') run ${GITHUB_RUN_ID:-local}" \
      && for i in 1 2 3; do git push -q origin HEAD:data && break; sleep 5; done ) || true
  echo "$(TZ=America/Los_Angeles date '+%H:%M:%S PT') state saved"
}

python3 supervisor.py >> data/supervisor.log 2>&1 &
SUP=$!
start=$(date +%s); last_save=$start
while kill -0 $SUP 2>/dev/null; do
  sleep 20
  now=$(date +%s)
  if [ $((now - last_save)) -ge "$SAVE_EVERY" ]; then
    grep -E "HEARTBEAT" data/bot.log | tail -n 1; save; last_save=$now
  fi
  [ $((now - start)) -ge "$RUN_SECONDS" ] && break
done
echo "slice over; stopping bot"
touch .slice_stop
for i in $(seq 1 40); do kill -0 $SUP 2>/dev/null || break; sleep 2; done
kill $SUP 2>/dev/null || true
rm -f .slice_stop
save
tail -n 8 data/supervisor.log
tail -n 8 data/bot.log
