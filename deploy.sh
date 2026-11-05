#!/usr/bin/env bash
# Command 1: deploy the inventory stack and start the scan.
#
#   ./deploy.sh --region ap-southeast-2 --bucket my-bucket --prefix FOLDER
#
# or with environment variables:
#
#   export S3_UPLOAD_URL=my-bucket INVENTORY_PREFIX=FOLDER
#   ./deploy.sh --region ap-southeast-2
#
# Required (all but --region can also come from the environment variables shown):
#   --region REGION   Region to deploy into and scan
#   --bucket BUCKET   S3 bucket name for the zip (S3_UPLOAD_URL)
#   --prefix FOLDER   Folder in the bucket for the zip (INVENTORY_PREFIX)
# Optional:
#   --role NAME       IAM role the scan runs as (ROLE_NAME; default six-pillar-diagram-role)
#   --stack NAME      CloudFormation stack name (STACK_NAME; default six-pillars-inventory)
#   -y, --yes         Do not ask to confirm
set -euo pipefail
cd "$(dirname "$0")"

die() { echo "ERROR: $*" >&2; exit 1; }
usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

STACK_NAME="${STACK_NAME:-six-pillars-inventory}"
ROLE_NAME="${ROLE_NAME:-six-pillar-diagram-role}"
REGION=""
URL="${S3_UPLOAD_URL:-}"
PREFIX="${INVENTORY_PREFIX:-}"
SSM_NAME=/six-pillars-inventory/inventory-prefix   # fixed; the stack always saves the prefix here
ASSUME_YES=false
TEMPLATE="cloudformation/six-pillars-inventory.template.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --region)  [ $# -ge 2 ] || die "--region needs a value"; REGION="$2"; shift 2 ;;
    --bucket)  [ $# -ge 2 ] || die "--bucket needs a value"; URL="$2"; shift 2 ;;
    --prefix)  [ $# -ge 2 ] || die "--prefix needs a value"; PREFIX="$2"; shift 2 ;;
    --role)    [ $# -ge 2 ] || die "--role needs a value"; ROLE_NAME="$2"; shift 2 ;;
    --stack)   [ $# -ge 2 ] || die "--stack needs a value"; STACK_NAME="$2"; shift 2 ;;
    --region=*) REGION="${1#*=}"; shift ;;
    --bucket=*) URL="${1#*=}"; shift ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    --role=*)   ROLE_NAME="${1#*=}"; shift ;;
    --stack=*)  STACK_NAME="${1#*=}"; shift ;;
    -y|--yes)  ASSUME_YES=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

# Options the follow-up commands need, so the hints we print can be copied as they are.
ARGS=" --region $REGION"
[ "$STACK_NAME" != six-pillars-inventory ] && ARGS+=" --stack $STACK_NAME"

export AWS_PAGER=""
[ -n "$REGION" ] || die "Give the Region to scan, for example: --region ap-southeast-2"
[[ "$REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || die "Not an AWS Region name: '$REGION'"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

# ---- where to upload: a bucket name (s3://bucket also works) and an optional folder
[ -n "$URL" ] || die "Give the S3 bucket for the zip, for example: --bucket my-bucket"
BUCKET="${URL#s3://}"
while [[ "$BUCKET" == */ ]]; do BUCKET="${BUCKET%/}"; done
[[ "$BUCKET" != */* ]] || die "Give only the bucket name in --bucket, and the folder with --prefix (got '$URL')"
while [[ "$PREFIX" == /* ]]; do PREFIX="${PREFIX#/}"; done
while [[ "$PREFIX" == */ ]]; do PREFIX="${PREFIX%/}"; done
[ -n "$PREFIX" ] || die "Give the folder in the bucket for the zip, for example: --prefix six-pillars-inventory"
[[ "$PREFIX" =~ ^[A-Za-z0-9!_.*\'\(\)-]+(/[A-Za-z0-9!_.*\'\(\)-]+)*$ ]] || die "Not a valid folder name: '$PREFIX' (letters, digits and !_.*'()- separated by single slashes)"
[[ "$BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || die "Not an S3 bucket name: '$BUCKET'"

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
    [ "$RUNNING" = 0 ] || die "A scan is already running. Check it with ./status.sh$ARGS"
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
Add codebuild.amazonaws.com to its trust policy, or use a different name: --role another-name"
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
echo "  Upload to:  s3://$BUCKET/$PREFIX/"
echo "  Prefix in:  Parameter Store $SSM_NAME"
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

# The stack starts the scan itself when it is created, and again whenever RunId changes,
# so a new RunId on every run is all it takes.
RUN_ID="run-$(date -u +%Y%m%dT%H%M%SZ)"

echo "Deploying stack $STACK_NAME and starting the scan (about 1-2 minutes)..."
if ! aws cloudformation deploy \
    --stack-name "$STACK_NAME" \
    --template-file "$TEMPLATE" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "InventoryBucket=$BUCKET" "InventoryPrefix=$PREFIX" \
                          "RoleName=$ROLE_NAME" "CreateRole=$CREATE_ROLE" "RunId=$RUN_ID"; then
  echo "Deploy failed. Reasons:" >&2
  aws cloudformation describe-stack-events --stack-name "$STACK_NAME" \
    --query "StackEvents[?contains(ResourceStatus,'FAILED')].[LogicalResourceId,ResourceStatusReason]" \
    --output text 2>/dev/null | head -5 >&2
  exit 1
fi

EXECUTION=$(output StartedExecutionArn)

echo
echo "Scan started. It usually takes 5-10 minutes."
echo "  Execution: $EXECUTION"
echo "  Prefix:    $PREFIX (saved in Parameter Store as $SSM_NAME)"
echo
echo "Check on it with:   ./status.sh$ARGS          (or ./status.sh$ARGS --watch to wait until it finishes)"
