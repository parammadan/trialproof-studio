#!/bin/bash
# Serve an open-weight model with vLLM (OpenAI-compatible, tool calling on) for TrialProof Studio on the
# Northeastern Explorer cluster. Self-ending so the GPU never sits idle: it stops when the client writes
# $D/done, or when no new chat request has arrived for IDLE_SECS. Submitted by hpc/connect.sh.
#SBATCH -J trialproof-vllm
#SBATCH -p gpu
#SBATCH --gres=gpu:a100:1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH -t 00:45:00
#SBATCH -o /scratch/%u/trialproof/serve.%j.log
export HF_HOME=${HF_HOME:-/scratch/$USER/.hf}
MODEL=${MODEL:-$(ls -d $HF_HOME/hub/models--Qwen--Qwen2.5-Coder-32B-Instruct-AWQ/snapshots/*/ | head -1)}
NAME=${NAME:-qwen2.5-coder-32b}
MAX_LEN=${MAX_LEN:-32768}
IDLE_SECS=${IDLE_SECS:-480}
D=/scratch/$USER/trialproof
LOG=$D/vllm.$SLURM_JOB_ID.log
rm -f $D/ready $D/done
t() { echo "[$(date +%T)] $*"; }

module load anaconda3/2024.06
source activate /scratch/$USER/envs/chaos   # vLLM 0.27; needs ~/.local (yaml, zmq)
# Unpack the environment to node-local disk: importing ~100k small files over NFS is slow on a cold node
PACK=$D/vllm-env.tar.zst
L=${TMPDIR:-/tmp}/tp-env-$SLURM_JOB_ID; mkdir -p $L
t "unpacking env"; zstd -d -T8 -q -c $PACK | tar -C $L -xf -
export PYTHONUSERBASE=$L/userbase; mkdir -p $PYTHONUSERBASE/lib && mv $L/python3.12 $PYTHONUSERBASE/lib/
export PATH=$L/chaos/bin:$PATH; PY=$L/chaos/bin/python
trap 'rm -rf $L' EXIT
export XDG_CACHE_HOME=$D/cache TRITON_CACHE_DIR=$D/cache/triton VLLM_CACHE_ROOT=$D/cache/vllm
mkdir -p $TRITON_CACHE_DIR $VLLM_CACHE_ROOT
export HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
# vLLM 0.27 is built for CUDA 13; the A100 driver is CUDA 12.8, so use NVIDIA's forward-compat libcuda
export LD_LIBRARY_PATH=/scratch/$USER/cuda-compat-13/usr/local/cuda-13.0/compat:$LD_LIBRARY_PATH
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

t "starting vLLM ($NAME)"
$PY -m vllm.entrypoints.cli.main serve "$MODEL" --served-model-name "$NAME" --host 0.0.0.0 --port 8100 \
  --max-model-len $MAX_LEN --gpu-memory-utilization 0.9 --enable-auto-tool-choice --tool-call-parser hermes \
  --enable-prefix-caching > $LOG 2>&1 &
V=$!
for i in $(seq 1 900); do
  # --noproxy: the cluster sets http_proxy, which would route a local request to the proxy
  if curl -sf --noproxy "*" http://127.0.0.1:8100/v1/models >/dev/null; then
    echo "$(hostname) $(date +%s)" > $D/ready; t "ready after ${i}s on $(hostname)"; break
  fi
  kill -0 $V 2>/dev/null || { t "vllm exited early"; tail -40 $LOG; exit 1; }
  sleep 1
done
[ -f $D/ready ] || { t "not ready in 900 s"; kill $V; exit 1; }

last=0; quiet=0
while [ ! -f $D/done ]; do
  n=$(grep -c "POST /v1/chat/completions" $LOG)
  if [ "$n" != "$last" ]; then last=$n; quiet=0; else quiet=$((quiet + 5)); fi
  [ $quiet -ge $IDLE_SECS ] && { t "stopping: no request for ${IDLE_SECS}s ($n served)"; break; }
  kill -0 $V 2>/dev/null || { t "vllm died"; tail -40 $LOG; exit 1; }
  sleep 5
done
[ -f $D/done ] && t "stopping: client done ($(grep -c 'POST /v1/chat/completions' $LOG) requests)"
kill $V; wait $V 2>/dev/null; exit 0
