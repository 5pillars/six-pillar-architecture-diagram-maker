# 6pillars.ai inventory scan

Runs the read-only inventory script (`collect_inventory.py`) inside an AWS account and uploads the zip to an
S3 location you choose. It runs entirely from AWS CloudShell, so there's nothing to install.

- [HOW-TO-RUN.txt](HOW-TO-RUN.txt): step-by-step instructions for customers, including troubleshooting
- [README.txt](README.txt): what the script and the stack do and don't do
- [ABOUT-THIS-SCRIPT.txt](ABOUT-THIS-SCRIPT.txt) and [iam-policy.json](iam-policy.json): what it reads and the permissions it needs
- [SAMPLE-DATA.txt](SAMPLE-DATA.txt): example output

## Quick start (AWS CloudShell)

1. In the AWS Console, pick the Region to scan, then open **CloudShell** (the `>_` icon in the top bar).
2. Get the files:
   ```bash
   git clone https://github.com/5pillars/sixpillar-diagram-inventory.git && cd sixpillar-diagram-inventory
   ```
3. Start the scan, then type `y` to confirm:
   ```bash
   ./deploy.sh --bucket example-bucket
   ```
4. Wait for it to finish (5–10 minutes):
   ```bash
   ./status.sh --watch
   ```
   When it shows `SUCCEEDED`, type `y` to delete the stack. The zip stays in S3:
   ```
   INVENTORY_S3_URI=s3://example-bucket/AWS-inventory-123456789012-ap-southeast-2-20261104T101500Z.zip
   Delete the stack now? The inventory zip stays in S3. [y/N]
   ```

The scan runs in AWS, not in CloudShell, so CloudShell can close while it runs. To check again, open
CloudShell in the same Region and run `cd sixpillar-diagram-inventory && ./status.sh --watch`. Run `./destroy.sh`
to delete the stack later.

To scan a different Region from CloudShell's, add `--region ap-southeast-2` to all three commands. To put
the zip in a folder, add `--prefix FOLDER`, or use `--bucket s3://example-bucket/FOLDER`.

### Commands

| Command        | What it does                                                                 |
|----------------|------------------------------------------------------------------------------|
| `./deploy.sh`  | Shows account, Region, upload location and role; deploys; starts the scan    |
| `./status.sh`  | RUNNING / SUCCEEDED / FAILED, recent log lines, the zip's S3 URI when done   |
| `./destroy.sh` | Stops a running scan, if any, and deletes the stack                          |

Each one prints `--help`. Their hints repeat whatever `--region` and `--stack` you used, so you can copy
them as they are.

`status.sh` exits with 0 when the scan has succeeded, 1 when it failed or no scan exists, and 2 while it
is still running, so you can use it in scripts.

### Options

An option overrides its environment variable, so either one works.

| Option            | Environment variable | Used by   | Default                   | Meaning                                       |
|-------------------|----------------------|-----------|---------------------------|-----------------------------------------------|
| `--region REGION` | `AWS_REGION`         | all three | CloudShell's Region       | Region to deploy into and scan                |
| `--bucket BUCKET` | `S3_UPLOAD_URL`      | deploy    | (required)                | `bucket`, or `s3://bucket/prefix`             |
| `--prefix PREFIX` |                      | deploy    | path in `--bucket`, or none | Folder in the bucket                     |
| `--role NAME`     | `ROLE_NAME`          | deploy    | `six-pillar-diagram-role` | IAM role the scan runs as                     |
| `--stack NAME`    | `STACK_NAME`         | all three | `six-pillars-inventory`   | CloudFormation stack name                     |
| `--watch`         |                      | status    |                           | Check every 30 seconds until the scan is done |
| `-y`, `--yes`     |                      | deploy, destroy |                     | Don't ask to confirm                          |

## What gets created

Six resources, all removed by `./destroy.sh`:

| Resource                           | Purpose                                                                     |
|------------------------------------|-----------------------------------------------------------------------------|
| IAM role `six-pillar-diagram-role` | The scan's identity (created only if it doesn't already exist)              |
| IAM policy on that role            | Read-only actions from `iam-policy.json`, log writing, `s3:PutObject` to the upload prefix |
| CodeBuild project                  | Runs `collect_inventory.py --yes` unchanged and uploads the zip             |
| Step Functions state machine       | Starts the CodeBuild job and waits for it (no Lambda 15-minute limit)       |
| Role for the state machine         | Lets it start and watch that one CodeBuild project                          |
| CloudWatch log group               | Scan output, kept 14 days                                                   |

There's no Lambda function, Docker image, VPC or CDK bootstrap. The script runs in CodeBuild's standard
Amazon Linux image, which already has Python 3 and AWS CLI v2. The script is embedded in the template, so
[cloudformation/six-pillars-inventory.template.json](cloudformation/six-pillars-inventory.template.json) is a
single self-contained file. It can also be deployed from the CloudFormation console: give it the bucket,
prefix and role name, then start the state machine.

**Permissions to deploy.** The person running `deploy.sh` needs to be able to create IAM roles and policies,
CodeBuild projects, Step Functions state machines and CloudWatch log groups through CloudFormation (for
example `AdministratorAccess`).

**Existing role.** If `six-pillar-diagram-role` already exists, `deploy.sh` uses it instead of creating
one. Its trust policy must allow `codebuild.amazonaws.com`. The stack attaches its scan policy to the role and
removes the policy again on delete. The role itself is left alone.

**Uploading to another account's bucket.** The zip is uploaded with `bucket-owner-full-control`. The
bucket's policy must allow `s3:PutObject` (and `s3:PutObjectAcl`) from
`arn:aws:iam::<customer account>:role/six-pillar-diagram-role`. If the bucket uses a KMS key, the role also
needs `kms:GenerateDataKey` on that key.

## Changing the template (CDK)

The template is generated by the CDK app in [cdk/](cdk/). After changing the CDK code, `iam-policy.json` or
the Python script, rebuild it and commit the result:

```bash
cd cdk
npm ci
npm run build-template   # writes ../cloudformation/six-pillars-inventory.template.json
```

The build fails if the embedded script grows past CodeBuild's 25,600-character inline buildspec limit
(currently about 19,000 characters).

## Running the script without the stack

`collect_inventory.py` still works on its own, in CloudShell or on your own computer, with no AWS resources
created. See options B and C in [HOW-TO-RUN.txt](HOW-TO-RUN.txt).
