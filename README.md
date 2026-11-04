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
   git clone https://github.com/5pillars/six-pillar-architecture-diagram-maker.git && cd six-pillar-architecture-diagram-maker
   ```
3. Start the scan, then type `y` to confirm. The Region, bucket and folder are all required:
   ```bash
   ./deploy.sh --region ap-southeast-2 --bucket example-bucket --prefix six-pillars-inventory
   ```
4. Wait for it to finish (5–10 minutes):
   ```bash
   ./status.sh --region ap-southeast-2 --watch
   ```
   When it shows `SUCCEEDED`, type `y` to delete the stack. The zip stays in S3:
   ```
   INVENTORY_S3_URI=s3://example-bucket/six-pillars-inventory/AWS-inventory-123456789012-ap-southeast-2-20261104T101500Z.zip
   Delete the stack now? The inventory zip stays in S3. [y/N]
   ```

The scan runs in AWS, not in CloudShell, so CloudShell can close while it runs. To check again, open
CloudShell in the same Region and run `cd six-pillar-architecture-diagram-maker && ./status.sh --region ap-southeast-2 --watch`. Run
`./destroy.sh --region ap-southeast-2` to delete the stack later.

The zip goes to `s3://BUCKET/FOLDER/`. The stack is created in, and scans, the Region given with
`--region`; give the same `--region` to `status.sh` and `destroy.sh`.

### Commands

| Command        | What it does                                                                 |
|----------------|------------------------------------------------------------------------------|
| `./deploy.sh`  | Shows account, Region, upload location and role; deploys; starts the scan    |
| `./status.sh`  | RUNNING / SUCCEEDED / FAILED, recent log lines, the zip's S3 URI when done   |
| `./destroy.sh` | Stops a running scan, if any, and deletes the stack                          |

Each one prints `--help`. Their hints repeat the `--region` and `--stack` you used, so you can copy
them as they are.

`status.sh` exits with 0 when the scan has succeeded, 1 when it failed or no scan exists, and 2 while it
is still running, so you can use it in scripts.

### Options

`deploy.sh` needs `--region`, `--bucket` and `--prefix`. The bucket and folder can come from their
environment variables instead; the Region must be given as an argument.

| Option            | Environment variable | Used by   | Default                   | Meaning                                       |
|-------------------|----------------------|-----------|---------------------------|-----------------------------------------------|
| `--region REGION` |                      | all three | (required for deploy)     | Region to deploy into and scan                |
| `--bucket BUCKET` | `S3_UPLOAD_URL`      | deploy    | (required, no default)    | Bucket name only                              |
| `--prefix FOLDER` | `INVENTORY_PREFIX`   | deploy    | (required, no default)    | Folder in the bucket                          |
| `--role NAME`     | `ROLE_NAME`          | deploy    | `six-pillar-diagram-role` | IAM role the scan runs as                     |
| `--stack NAME`    | `STACK_NAME`         | all three | `six-pillars-inventory`   | CloudFormation stack name                     |
| `--watch`         |                      | status    |                           | Check every 30 seconds until the scan is done |
| `-y`, `--yes`     |                      | deploy, destroy |                     | Don't ask to confirm                          |

## What gets created

These resources are all removed by `./destroy.sh`:

| Resource                           | Purpose                                                                     |
|------------------------------------|-----------------------------------------------------------------------------|
| IAM role `six-pillar-diagram-role` | The scan's identity (created only if it doesn't already exist)              |
| IAM policy on that role            | Read-only actions from `iam-policy.json`, log writing, `s3:PutObject` to the upload folder |
| CodeBuild project                  | Runs `collect_inventory.py --yes` unchanged and uploads the zip             |
| Step Functions state machine       | Starts the CodeBuild job and waits for it (no Lambda 15-minute limit)       |
| Role for the state machine         | Lets it start and watch that one CodeBuild project                          |
| Starter Lambda function and role   | Starts the scan when the stack is created; stops a running scan when the stack is deleted |
| CloudWatch log group               | Scan and starter output, kept 14 days                                       |

The starter Lambda runs for a second or two; the scan itself runs in CodeBuild, so there's no Lambda
15-minute limit. There's no Docker image, VPC or CDK bootstrap. The script runs in CodeBuild's standard
Amazon Linux image, which already has Python 3 and AWS CLI v2. The script is embedded in the template, so
[cloudformation/six-pillars-inventory.template.json](cloudformation/six-pillars-inventory.template.json) is a
single self-contained file.

**Deploying from the CloudFormation console or CLI.** The scan starts by itself as soon as the stack is
created: give the template the bucket name (`InventoryBucket`) and folder (`InventoryPrefix`), both required, and
that's all. Create the stack in the Region to scan. To run it again, update the
stack with a new value for `RunId` (`deploy.sh` does this for you). Deleting the stack stops a scan that's
still running.

**Permissions to deploy.** The person deploying needs to be able to create IAM roles and policies, CodeBuild
projects, Step Functions state machines, Lambda functions and CloudWatch log groups through CloudFormation
(for example `AdministratorAccess`).

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

### Publishing the template

`cdk/scripts/publish-template.sh` uploads the template to an environment's template bucket. Give the
environment as the first argument:

| Environment | Destination                                                                               |
|-------------|-------------------------------------------------------------------------------------------|
| `uat`       | `s3://uat-six-pillars-user-role-templates/templates/six-pillars-inventory.template.json`  |
| `prod`      | `s3://prod-six-pillars-user-role-templates/templates/six-pillars-inventory.template.json` |

```bash
cdk/scripts/publish-template.sh uat --profile uat-profile     # add --build to rebuild first, -y to skip the prompt
cdk/scripts/publish-template.sh prod --profile prod-profile
```

It shows the account you're signed in to and the destination, and asks before uploading.

## Running the script without the stack

`collect_inventory.py` still works on its own, in CloudShell or on your own computer, with no AWS resources
created. See options B and C in [HOW-TO-RUN.txt](HOW-TO-RUN.txt).
