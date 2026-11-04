#!/usr/bin/env bash
# Command 1: deploy the inventory stack and start the scan.
#
#   export S3_UPLOAD_URL=s3://your-bucket/optional/prefix
#   ./deploy.sh            # add -y to skip the confirmation
#
# Optional: STACK_NAME (default six-pillars-inventory), ROLE_NAME (default six-pillar-diagram-role).
# The Region is the one CloudShell is in (AWS_REGION), or set AWS_REGION yourself.
set -euo pipefail
cd "$(dirname "$0")"

STACK_NAME="${STACK_NAME:-six-pillars-inventory}"
ROLE_NAME="${ROLE_NAME:-six-pillar-diagram-role}"
TEMPLATE="cloudformation/six-pillars-inventory.template.json"
ASSUME_YES=false
[ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ] && ASSUME_YES=true

die() { echo "ERROR: $*" >&2; exit 1; }

export AWS_PAGER=""
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
[ -n "$REGION" ] || die "No Region set. Run in CloudShell, or: export AWS_REGION=ap-southeast-2"
export AWS_REGION="$REGION"

# ---- where to upload: s3://bucket/prefix (or bucket/prefix)
URL="${S3_UPLOAD_URL:-}"
[ -n "$URL" ] || die "Set the upload location first, for example: export S3_UPLOAD_URL=s3://my-bucket/six-pillars-inventory"
URL="${URL#s3://}"
URL="${URL#/}"
BUCKET="${URL%%/*}"
PREFIX=""
[[ "$URL" == */* ]] && PREFIX="${URL#*/}"
while [[ "$PREFIX" == */ ]]; do PREFIX="${PREFIX%/}"; done
[[ "$BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || die "Not an S3 bucket name: '$BUCKET' (from S3_UPLOAD_URL)"

# ---- who and where
IDENTITY=$(aws sts get-caller-identity --query '[Account,Arn]' --output text) || die "Not signed in to AWS."
ACCOUNT=$(echo "$IDENTITY" | cut -f1)
CALLER=$(echo "$IDENTITY" | cut -f2)

stack_status() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo NONE
}
output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

STATUS=$(stack_status)
case "$STATUS" in
  *_IN_PROGRESS) die "Stack $STACK_NAME is busy ($STATUS). Wait, then try again." ;;
esac

# Don't start a second scan while one is running.
if [ "$STATUS" != NONE ] && [ "$STATUS" != ROLLBACK_COMPLETE ]; then
  MACHINE=$(output StateMachineArn 2>/dev/null || true)
  if [ -n "$MACHINE" ] && [ "$MACHINE" != None ]; then
    RUNNING=$(aws stepfunctions list-executions --state-machine-arn "$MACHINE" --status-filter RUNNING \
      --query 'length(executions)' --output text 2>/dev/null || echo 0)
    [ "$RUNNING" = 0 ] || die "A scan is already running. Check it with ./status.sh"
  fi
fi

# ---- the role: reuse six-pillar-diagram-role if it exists, otherwise the stack creates it
if [ "$STATUS" != NONE ] && [ "$STATUS" != ROLLBACK_COMPLETE ]; then
  # Keep what the existing stack decided, so an update never deletes a role it created.
  CREATE_ROLE=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Parameters[?ParameterKey=='CreateRole'].ParameterValue" --output text)
else
  if ROLE_ERR=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.AssumeRolePolicyDocument' --output json 2>&1); then
    CREATE_ROLE=false
    echo "$ROLE_ERR" | grep -q 'codebuild.amazonaws.com' || die "Role $ROLE_NAME exists but CodeBuild cannot use it.
Add codebuild.amazonaws.com to its trust policy, or use a different name: export ROLE_NAME=another-name"
  elif echo "$ROLE_ERR" | grep -q NoSuchEntity; then
    CREATE_ROLE=true
  else
    die "Could not check role $ROLE_NAME: $ROLE_ERR"
  fi
fi

echo "6pillars.ai inventory scan (read-only; no IAM roles, no cost data)"
echo "  Account:    $ACCOUNT"
echo "  Signed in:  $CALLER"
echo "  Region:     $REGION"
echo "  Upload to:  s3://$BUCKET/${PREFIX:+$PREFIX/}"
echo "  Role:       $ROLE_NAME ($([ "$CREATE_ROLE" = true ] && echo 'created by the stack' || echo 'existing role'))"
echo "  Stack:      $STACK_NAME"
if ! $ASSUME_YES; then
  read -r -p "Deploy the stack and start the scan? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo "Stopped: nothing was deployed."; exit 0; }
fi

# A stack whose first create failed must be deleted before it can be created again.
if [ "$STATUS" = ROLLBACK_COMPLETE ]; then
  echo "Removing the stack left over from a failed deploy..."
  aws cloudformation delete-stack --stack-name "$STACK_NAME"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
fi

echo "Deploying stack $STACK_NAME (about 1-2 minutes)..."
if ! aws cloudformation deploy \
    --stack-name "$STACK_NAME" \
    --template-file "$TEMPLATE" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset \
    --parameter-overrides "InventoryBucket=$BUCKET" "InventoryPrefix=$PREFIX" \
                          "RoleName=$ROLE_NAME" "CreateRole=$CREATE_ROLE"; then
  echo "Deploy failed. Reasons:" >&2
  aws cloudformation describe-stack-events --stack-name "$STACK_NAME" \
    --query "StackEvents[?contains(ResourceStatus,'FAILED')].[LogicalResourceId,ResourceStatusReason]" \
    --output text 2>/dev/null | head -5 >&2
  exit 1
fi

MACHINE=$(output StateMachineArn)
EXECUTION=$(aws stepfunctions start-execution --state-machine-arn "$MACHINE" \
  --name "scan-$(date -u +%Y%m%dT%H%M%SZ)" --query executionArn --output text)

echo
echo "Scan started. It usually takes 5-10 minutes."
echo "  Execution: $EXECUTION"
echo
echo "Check on it with:   ./status.sh          (or ./status.sh --watch to wait until it finishes)"
