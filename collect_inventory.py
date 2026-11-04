#!/usr/bin/env python3
"""6pillars.ai Inventory Script for one AWS Region. Python 3.9+ and AWS CLI v2.

Reads the inventory of the AWS account you are signed in to, in one Region, and zips it. Send the zip back to us;
we draw the architecture diagram from it. Only read-only calls are made: nothing in the account is changed, and
secret values, Lambda environment values and S3 file contents are never read.

The Region is --region, or else the Region you are working in (the Console Region in AWS CloudShell).
Account-wide services that have no Region (CloudFront, Route 53, Global Accelerator) are read too, because they
connect to the Region's resources. S3 buckets outside the Region are dropped from the inventory.
This version reads no IAM data (no roles, trust policies, permission policies or instance profiles) and no cost
data (no prices, budgets or billing).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))   # use the inventory/ folder next to this script

from inventory.aws import AWS
from inventory.graph import build_graph
from inventory.scan import run_scan


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--region', help='AWS Region to scan, for example ap-southeast-2 '
                                         '(default: the Region you are working in, from AWS_REGION)')
    parser.add_argument('--profile', help='AWS CLI profile to use (default: the credentials you are signed in with)')
    parser.add_argument('--yes', action='store_true', help='Do not ask to confirm the account before the scan')
    parser.add_argument('--aws-cli', help='AWS CLI executable path, if aws is not on the PATH')
    parser.add_argument('--max-rate', type=float, default=3.0,
                        help='Most AWS CLI commands started per second (default 3; 0 = no limit)')
    parser.add_argument('--output', default='outputs', help='Folder for the inventory and the zip (default: outputs)')
    args = parser.parse_args()
    if args.max_rate < 0:
        parser.error('--max-rate cannot be negative')
    # CloudShell sets AWS_REGION to the Region selected in the Console.
    region = args.region or os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    if not region:
        parser.error('say which Region to scan, for example: --region ap-southeast-2')
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', region):
        parser.error(f'not an AWS Region name: {region}')

    aws = AWS(args.profile, region, args.aws_cli, None, args.max_rate)
    identity = aws.identity()
    account = identity['Account']
    print('6pillars.ai Inventory Script (read-only; no IAM roles, no cost data)', flush=True)
    print(f'Signed in to AWS account {account} as {identity.get("Arn", "")}', flush=True)
    print(f'Region to scan: {region}', flush=True)
    if not args.yes and input('Collect the inventory of this account and Region? [y/N] ').strip().lower() not in ('y', 'yes'):
        print('Stopped: nothing was collected.')
        return
    # Every call from here on must reach the same account.
    aws.expected_account = account

    last = ['']
    def progress(message, done, total):
        if message != last[0] or (total and (done % 25 == 0 or done == total)):
            print(f'{message} ({done}/{total})', flush=True)
            last[0] = message
    snapshot = run_scan(aws, progress, [region])

    # list-buckets returns every bucket in the account; keep only the ones in the Region.
    dropped = 0
    for record in snapshot['records']:
        if record['kind'] == 'S3':
            kept = [b for b in record['resources'] if b.get('region') == region]
            dropped += len(record['resources']) - len(kept)
            record['resources'] = kept
    snapshot['graph'] = build_graph(snapshot['records'], snapshot['relationships'], account)
    snapshot['coverage'] = [dict(region=r['region'], kind=r['kind'], resources=len(r['resources']), success=r['success'])
                            for r in snapshot['records']]
    snapshot['handoff'] = {'account_guard': account, 'profile': args.profile or '(signed-in credentials)',
                           'read_only': True, 'billing_requested': False, 'iam_roles_read': False,
                           'cost_data_read': False, 'region_only': region,
                           's3_buckets_outside_region_dropped': dropped,
                           'status': 'partial' if snapshot['errors'] else 'completed selected checks'}

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = Path(args.output).resolve() / account / stamp
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'snapshot.json').write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding='utf-8')
    (directory / 'scan-errors.json').write_text(json.dumps(snapshot['errors'], indent=2), encoding='utf-8')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}
    (directory / 'manifest.sha256.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    archive = directory.parent / f'AWS-inventory-{account}-{region}-{stamp}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(directory.iterdir()):
            if p.is_file():
                z.write(p, p.name)

    nodes = len(snapshot['graph']['nodes'])
    print(f'Resources found: {nodes}; failed calls: {len(snapshot["errors"])} (listed in scan-errors.json)', flush=True)
    print('REPORT_DIRECTORY=' + str(directory), flush=True)
    print('RETURN_THIS_ZIP=' + str(archive), flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped.', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print('Inventory failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
