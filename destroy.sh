#!/usr/bin/env bash
# Deletes the inventory stack: the CodeBuild project, state machine, log group and the role's
# scan policy (and the role itself if the stack created it). The inventory zip stays in S3.
#
#   ./destroy.sh                          # add -y to skip the confirmation
#   ./destroy.sh --region ap-southeast-2  # if you deployed with --region
#
# Options: --region REGION, --stack NAME (as given to deploy.sh), -y/--yes
set -euo pipefail

STACK_NAME="${STACK_NAME:-six-pillars-inventory}"
ASSUME_YES=false
while [ $# -gt 0 ]; do
  case "$1" in
    --region) [ $# -ge 2 ] || { echo "--region needs a value" >&2; exit 1; }
              export AWS_REGION="$2" AWS_DEFAULT_REGION="$2"; shift 2 ;;
    --stack)  [ $# -ge 2 ] || { echo "--stack needs a value" >&2; exit 1; }
              STACK_NAME="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=true; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
done
export AWS_PAGER=""

if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
  echo "Stack $STACK_NAME is not deployed in ${AWS_REGION:-this Region}. Nothing to delete."
  exit 0
fi

MACHINE=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" --output text 2>/dev/null || true)
RUNNING=""
if [ -n "$MACHINE" ] && [ "$MACHINE" != None ]; then
  RUNNING=$(aws stepfunctions list-executions --state-machine-arn "$MACHINE" --status-filter RUNNING \
    --query 'executions[].executionArn' --output text 2>/dev/null || true)
  [ "$RUNNING" = None ] && RUNNING=""
fi

if ! $ASSUME_YES; then
  [ -n "$RUNNING" ] && echo "WARNING: a scan is still running; it will be stopped and no zip will be uploaded."
  read -r -p "Delete stack $STACK_NAME? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo "Stopped: nothing was deleted."; exit 0; }
fi

for execution in $RUNNING; do
  echo "Stopping running scan $execution"
  aws stepfunctions stop-execution --execution-arn "$execution" --cause "Stack deleted" >/dev/null
done

echo "Deleting stack $STACK_NAME..."
aws cloudformation delete-stack --stack-name "$STACK_NAME"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
echo "Deleted. The inventory zip is still in your S3 bucket."
