#!/usr/bin/env bash
# Command 2: is the scan still running? When it has finished, shows where the zip was uploaded
# and offers to delete the stack.
#
#   ./status.sh                          # check once
#   ./status.sh --watch                  # check every 30 seconds until the scan finishes
#   ./status.sh --region ap-southeast-2  # if you deployed with --region
#
# Options: --region REGION, --stack NAME (as given to deploy.sh), -w/--watch
#
# Exit code: 0 finished OK, 1 failed or no scan, 2 still running.
set -euo pipefail
cd "$(dirname "$0")"

STACK_NAME="${STACK_NAME:-six-pillars-inventory}"
WATCH=false
PASS=()   # options handed on to destroy.sh and shown in hints
while [ $# -gt 0 ]; do
  case "$1" in
    --region) [ $# -ge 2 ] || { echo "--region needs a value" >&2; exit 1; }
              export AWS_REGION="$2" AWS_DEFAULT_REGION="$2"; PASS+=(--region "$2"); shift 2 ;;
    --stack)  [ $# -ge 2 ] || { echo "--stack needs a value" >&2; exit 1; }
              STACK_NAME="$2"; PASS+=(--stack "$2"); shift 2 ;;
    -w|--watch) WATCH=true; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
done
ARGS="${PASS[*]:+ ${PASS[*]}}"
export AWS_PAGER=""

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

if ! MACHINE=$(output StateMachineArn 2>/dev/null) || [ -z "$MACHINE" ] || [ "$MACHINE" = None ]; then
  echo "Stack $STACK_NAME is not deployed in ${AWS_REGION:-this Region}. Start with ./deploy.sh (or add --region where you deployed it)"
  exit 1
fi
LOG_GROUP=$(output LogGroupName)

offer_destroy() {
  echo
  if [ -t 0 ]; then
    read -r -p "Delete the stack now? The inventory zip stays in S3. [y/N] " answer
    if [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
      exec ./destroy.sh --yes ${PASS[@]+"${PASS[@]}"}
    fi
  fi
  echo "Delete the stack later with:   ./destroy.sh$ARGS"
}

while true; do
  EXECUTION=$(aws stepfunctions list-executions --state-machine-arn "$MACHINE" --max-items 1 \
    --query 'executions[0].executionArn' --output text | head -n 1)
  if [ -z "$EXECUTION" ] || [ "$EXECUTION" = None ]; then
    echo "No scan has been started. Run ./deploy.sh"
    exit 1
  fi

  DETAILS=$(aws stepfunctions describe-execution --execution-arn "$EXECUTION" --output json)
  read -r STATUS ELAPSED < <(echo "$DETAILS" | python3 -c '
import json, re, sys
from datetime import datetime, timezone
d = json.load(sys.stdin)
def when(v):
    # Python 3.9 (CloudShell) only parses 3- or 6-digit fractions, so drop them.
    if isinstance(v, str):
        return datetime.fromisoformat(re.sub(r"\.\d+", "", v).replace("Z", "+00:00"))
    return datetime.fromtimestamp(v, timezone.utc)
start = when(d["startDate"])
stop = when(d["stopDate"]) if d.get("stopDate") else datetime.now(timezone.utc)
m, s = divmod(int((stop - start).total_seconds()), 60)
print(d["status"], f"{m}m{s:02d}s")
')

  case "$STATUS" in
    RUNNING)
      echo "$(date -u +%H:%M:%S) RUNNING for $ELAPSED. Latest scan output:"
      aws logs tail "$LOG_GROUP" --since 30m --format short 2>/dev/null | tail -n 3 | sed 's/^/    /' || true
      if $WATCH; then sleep 30; continue; fi
      echo "Still running. Run ./status.sh$ARGS again in a minute, or ./status.sh$ARGS --watch"
      exit 2
      ;;
    SUCCEEDED)
      URI=$(echo "$DETAILS" | python3 -c '
import json, sys
build = json.loads(json.load(sys.stdin).get("output") or "{}").get("Build", {})
for v in build.get("ExportedEnvironmentVariables") or build.get("exportedEnvironmentVariables") or []:
    if (v.get("Name") or v.get("name")) == "INVENTORY_S3_URI":
        print(v.get("Value") or v.get("value") or "")
')
      if [ -z "$URI" ]; then
        URI=$(aws logs tail "$LOG_GROUP" --since 1d --format short 2>/dev/null \
          | sed -n 's/.*INVENTORY_S3_URI=\(s3:[^ ]*\).*/\1/p' | tail -n 1 || true)
      fi
      echo "SUCCEEDED in $ELAPSED."
      echo
      echo "INVENTORY_S3_URI=$URI"
      # A download link for the person running this (works only if they can read the bucket).
      if LINK=$(aws s3 presign "$URI" --expires-in 3600 2>/dev/null); then
        echo "Download link (valid 1 hour): $LINK"
      fi
      offer_destroy
      exit 0
      ;;
    *)
      echo "$STATUS after $ELAPSED."
      echo "$DETAILS" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if d.get("error"): print("  Error:", d["error"])
if d.get("cause"): print("  Cause:", d["cause"][:600])
'
      echo "Last lines of the scan log:"
      aws logs tail "$LOG_GROUP" --since 1d --format short 2>/dev/null | tail -n 15 | sed 's/^/    /' || true
      echo
      echo "Fix the problem and run ./deploy.sh again, or remove everything with ./destroy.sh$ARGS"
      offer_destroy
      exit 1
      ;;
  esac
done
