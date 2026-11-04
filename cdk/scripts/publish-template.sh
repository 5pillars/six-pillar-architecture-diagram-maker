#!/usr/bin/env bash
# Uploads the CloudFormation template to the template bucket of an environment:
#
#   uat   ->  s3://uat-six-pillars-user-role-templates/templates/six-pillars-inventory.template.json
#   prod  ->  s3://prod-six-pillars-user-role-templates/templates/six-pillars-inventory.template.json
#
# Usage: cdk/scripts/publish-template.sh uat|prod [--profile NAME] [--build] [-y]
#   uat|prod         Environment to publish to (or --env uat|prod)
#   --profile NAME   AWS CLI profile to use (default: the signed-in credentials)
#   --build          Rebuild the template with CDK before uploading
#   -y, --yes        Do not ask to confirm
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="$ROOT/cloudformation/six-pillars-inventory.template.json"
KEY="templates/six-pillars-inventory.template.json"

die() { echo "ERROR: $*" >&2; exit 1; }
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

ENVIRONMENT=""
BUILD=false
ASSUME_YES=false
PROFILE_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    uat|prod)  ENVIRONMENT="$1"; shift ;;
    --env)     [ $# -ge 2 ] || die "--env needs a value"; ENVIRONMENT="$2"; shift 2 ;;
    --env=*)   ENVIRONMENT="${1#*=}"; shift ;;
    --profile) [ $# -ge 2 ] || die "--profile needs a value"; PROFILE_ARGS=(--profile "$2"); shift 2 ;;
    --build)   BUILD=true; shift ;;
    -y|--yes)  ASSUME_YES=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done
[ -n "$ENVIRONMENT" ] || { echo "Say which environment: uat or prod" >&2; usage 1; }
case "$ENVIRONMENT" in uat|prod) ;; *) die "Environment must be uat or prod, not '$ENVIRONMENT'" ;; esac

aws_() { aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} "$@"; }
export AWS_PAGER=""

if $BUILD; then
  (cd "$ROOT/cdk" && npm run --silent build-template)
fi
[ -f "$TEMPLATE" ] || die "No template at $TEMPLATE. Run with --build."

IDENTITY=$(aws_ sts get-caller-identity --query '[Account,Arn]' --output text) || die "Not signed in to AWS."
ACCOUNT=$(echo "$IDENTITY" | cut -f1)
CALLER=$(echo "$IDENTITY" | cut -f2)

BUCKET="$ENVIRONMENT-six-pillars-user-role-templates"
SHA=$(shasum -a 256 "$TEMPLATE" | cut -d' ' -f1)

echo "Publish the inventory template to $ENVIRONMENT"
echo "  Account:   $ACCOUNT"
echo "  Signed in: $CALLER"
echo "  File:      ${TEMPLATE#$ROOT/} ($(wc -c < "$TEMPLATE" | tr -d ' ') bytes, sha256 ${SHA:0:12}...)"
echo "  To:        s3://$BUCKET/$KEY"
if ! $ASSUME_YES; then
  read -r -p "Upload? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo "Stopped: nothing was uploaded."; exit 0; }
fi

aws_ s3 cp "$TEMPLATE" "s3://$BUCKET/$KEY" --content-type application/json \
  --metadata "sha256=$SHA" --no-progress

REGION=$(aws_ s3api get-bucket-location --bucket "$BUCKET" --query LocationConstraint --output text 2>/dev/null || true)
case "$REGION" in ""|None|null) REGION=us-east-1 ;; EU) REGION=eu-west-1 ;; esac
echo
echo "Uploaded."
echo "  S3 URI:       s3://$BUCKET/$KEY"
echo "  Template URL: https://$BUCKET.s3.$REGION.amazonaws.com/$KEY"
