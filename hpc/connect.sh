#!/bin/bash
# Start vLLM on Explorer, tunnel it to localhost:8100 and point TrialProof Studio at it.
#   sh hpc/connect.sh          start, connect, keep the tunnel open until Ctrl-C (then the job stops)
#   sh hpc/connect.sh stop     stop the server and the tunnel
# The job also stops itself after IDLE_SECS (default 480) without a request.
set -u
HOST=${TP_HPC_HOST:-explorer}
D=/scratch/madan.pa/trialproof
PORT=${TP_LOCAL_PORT:-8100}
API=${TP_API:-http://127.0.0.1:8765}
HERE=$(cd "$(dirname "$0")" && pwd)
PIDF=$HERE/.tunnel.pid
JOBF=$HERE/.job

stop() {
  ssh $HOST "touch $D/done" 2>/dev/null
  [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"
  if [ -f "$JOBF" ]; then
    J=$(cat "$JOBF"); sleep 45; ssh $HOST "scancel $J" 2>/dev/null; rm -f "$JOBF"
  fi
  echo "stopped"
}
[ "${1:-}" = "stop" ] && { stop; exit 0; }

scp -q "$HERE/serve.sbatch" $HOST:$D/serve.sbatch || exit 1
# Explorer's controller now and then answers "Unexpected message received"; retry a few times
for try in 1 2 3 4; do
  J=$(ssh $HOST "rm -f $D/ready $D/done; sbatch --parsable ${TP_SBATCH_ARGS:-} $D/serve.sbatch") && break
  # a timed-out sbatch may still have queued the job: adopt it rather than submit a duplicate
  sleep 30; J=$(ssh $HOST "squeue -h -u \$USER -n trialproof-vllm -o %i" | tail -1)
  [ -n "$J" ] && { echo "sbatch reported an error but job $J was queued; using it"; break; }
  [ $try = 4 ] && exit 1; echo "sbatch failed; retrying"
done
echo "$J" > "$JOBF"
echo "submitted job $J; waiting for the model (queue + ~3 min load)"
trap 'echo; stop; exit 0' INT TERM
for i in $(seq 1 360); do
  # the ready file (on shared scratch, tagged with the job id) needs no Slurm call, which can be slow
  case "$(ssh $HOST "cat $D/ready 2>/dev/null")" in *" $J") break ;; esac
  st=$(ssh $HOST "timeout 20 squeue -h -j $J -o %T 2>/dev/null")
  if [ -z "$st" ]; then
    # squeue can come back empty for a moment; only sacct saying the job is over counts as "ended"
    state=$(ssh $HOST "timeout 20 sacct -n -X -j $J -o State 2>/dev/null" | tr -d ' ')
    case "$state" in PENDING*|RUNNING*|"") sleep 10; continue ;; esac
    echo "job $J ended before it was ready ($state):"; ssh $HOST "tail -25 $D/serve.$J.log"; ssh $HOST "scancel $J" 2>/dev/null; rm -f "$JOBF"; exit 1
  fi
  printf "\r  %-60s" "$st (${i}0s)"; sleep 10
done
NODE=$(ssh $HOST "cut -d' ' -f1 $D/ready")
echo; echo "ready on $NODE; opening tunnel localhost:$PORT -> $NODE:8100"
# own connection, not a shared ControlMaster: a forward added to the master outlives this process
ssh -N -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes -L $PORT:$NODE:8100 $HOST & echo $! > "$PIDF"
sleep 5
curl -sf --noproxy '*' http://127.0.0.1:$PORT/v1/models | head -c 300 | grep . || { echo "model not reachable on localhost:$PORT (port in use?)"; stop; exit 1; }
echo
curl -sf -X PUT "$API/api/settings/llm" -H 'Content-Type: application/json' \
  -d "{\"base_url\":\"http://127.0.0.1:$PORT/v1\",\"model\":\"${NAME:-qwen2.5-coder-32b}\"}" >/dev/null \
  && echo "TrialProof Studio now uses http://127.0.0.1:$PORT/v1" || echo "(start the TrialProof server, then set the endpoint in Settings)"
echo "Ctrl-C to stop the model and free the GPU."
wait
