6PILLARS.AI INVENTORY SCRIPT: ONE ACCOUNT, ONE REGION
=====================================================

This script reads the inventory of one AWS account, in one AWS Region, and puts it in a zip file. We use
that zip to draw your architecture diagram.

There are two ways to run it. HOW-TO-RUN.txt gives step-by-step instructions for both:

  A. CloudFormation stack (recommended). From AWS CloudShell, one command deploys a small temporary stack
     that runs the script inside your account and uploads the zip to an S3 location. A second command shows
     whether it has finished. When it has, you delete the stack. CloudShell does not need to stay open
     while it runs.

  B. Run the script yourself, in AWS CloudShell or on your own computer, and send us the zip.

ABOUT-THIS-SCRIPT.txt explains what it reads and the permissions it needs. SAMPLE-DATA.txt shows example
data and which parts of it may be sensitive.

What the script does and does not do
  - It only reads. Nothing in your account is created, changed, started or deleted by the scan itself.
  - It never reads secret values, Lambda environment variables or the contents of S3 files.
  - It scans the account you are signed in to, and stops if that account changes during the scan.
  - It scans one Region: the one you choose with --region, or else the Region you are working in (in
    AWS CloudShell, the Region selected in the Console). A few services have no Region and are read for the
    whole account because they connect to that Region's resources: CloudFront, Route 53 and Global
    Accelerator. S3 buckets in other Regions are left out of the zip.
  - It does not read IAM at all: no roles, role policies, trust policies or instance profiles.
  - It does not read any cost data: no prices, budgets or billing.
  - The zip holds three files: snapshot.json (the inventory), scan-errors.json (any calls that failed) and
    manifest.sha256.json (checksums).

What the CloudFormation stack (option A) creates
  The stack is the only thing that adds anything to your account, and ./destroy.sh removes all of it:
  - An IAM role named six-pillar-diagram-role with the read-only permissions in iam-policy.json, plus
    permission to write its log and to upload the zip to the S3 location you give. If a role with that
    name already exists, it is used instead, and the stack only adds (then removes) its permissions.
  - A CodeBuild project that runs this script, unchanged, and uploads the zip.
  - A Step Functions state machine that runs the scan and waits for it to finish, plus a small role
    that lets it do so.
  - A small Lambda function, and its role, that starts the scan as soon as the stack is created (and
    stops it if the stack is deleted while it is still running).
  - A CloudWatch log group with the scan's progress messages (kept 14 days).
  - A Parameter Store (SSM) parameter, /six-pillars-inventory/inventory-prefix, whose value is the
    folder the zip is uploaded to.
  The zip stays in the S3 bucket after the stack is deleted.


1. WHAT YOU NEED
----------------
Option A (CloudFormation stack, in AWS CloudShell)
  - Signed in to the AWS Console for the account to be read, with permission to create IAM roles,
    CodeBuild projects, Step Functions state machines, Lambda functions, CloudWatch log groups and
    Parameter Store parameters through CloudFormation
    (for example the AWS managed policy AdministratorAccess).
  - The name of an S3 bucket to upload the zip to, for example  example-bucket  (we will give you
    one, or you can use a bucket of your own). You also give the folder in the bucket for the zip, for example  six-pillars-inventory.

Option B (run the script yourself)
  - Python 3.9 or later (no extra packages). AWS CloudShell already has it.
  - AWS CLI version 2.
  - Signed in to the AWS account, with the read-only permissions in iam-policy.json (the AWS managed
    policy ReadOnlyAccess also works). Check which account you are signed in to with:
        aws sts get-caller-identity
  - Internet access to the AWS API endpoints.


2. RUN IT
---------
Option A, in AWS CloudShell (pick the Region to scan in the Console first):

    git clone https://github.com/5pillars/six-pillar-architecture-diagram-maker.git && cd six-pillar-architecture-diagram-maker
    ./deploy.sh --region ap-southeast-2 --bucket example-bucket --prefix six-pillars-inventory
                                     starts the scan (type y to confirm); all three are required
    ./status.sh --region ap-southeast-2 --watch
                                     waits until it finishes, then offers to delete the stack

That's it. ./destroy.sh --region ap-southeast-2 deletes the stack later if you didn't then. HOW-TO-RUN.txt has the details and
the other options (--ssm-name, --role, --stack).

Option B, in a terminal in this folder:

    python collect_inventory.py --region REGION

for example  --region ap-southeast-2  (on macOS or Linux it may be python3). In AWS CloudShell you can
leave out --region: the Region selected in the Console is used. The script shows the account, the identity
you are signed in as and the Region, and asks you to confirm before it starts. It takes about 5 to 10
minutes.

If you sign in with a named AWS CLI profile, add it:

    python collect_inventory.py --region REGION --profile YOUR_PROFILE

Other options
    --yes            Do not ask to confirm the account
    --output DIR     Where to write the inventory and the zip (default: outputs)
    --aws-cli PATH   AWS CLI executable, if aws is not on the PATH
    --max-rate N     Most AWS CLI commands started per second (default 3; lower it if you see throttling)


3. THE ZIP
----------
Option A: when the scan finishes, ./status.sh prints where the zip was uploaded:

    INVENTORY_S3_URI=s3://example-bucket/six-pillars-inventory/AWS-inventory-<account>-<region>-<time>.zip

If the S3 location is one we gave you, there is nothing to send: tell us it has finished.

Option B: when it finishes, the script prints two lines:

    REPORT_DIRECTORY=...   the folder with the files
    RETURN_THIS_ZIP=...    the zip to send us, named AWS-inventory-<account>-<region>-<time>.zip

Send us the zip.

Either way, some failed calls are normal (for example a permission that is missing); they are listed in
scan-errors.json so we know what could not be read.


TROUBLESHOOTING
---------------
  - "Unable to locate credentials" or "expired token": sign in again (for example aws sso login or
    aws login, or close and reopen CloudShell), then run the command again.
  - "AccessDenied" in scan-errors.json: the identity is missing a permission from iam-policy.json. The
    rest of the inventory is still collected.
  - Calls time out: check that your network can reach https://ec2.REGION.amazonaws.com, and that the
    Region is enabled in the account (some Regions are opt-in: Account settings > AWS Regions).
  - Option B only. CloudShell closed before the script finished: CloudShell ends a session after 20 to
    30 minutes without typing or clicking. Run it again and click in the window every few minutes.
    (Option A is not affected: the scan runs in your account, not in CloudShell.)
  - Option A problems: see "IF SOMETHING GOES WRONG" in HOW-TO-RUN.txt.
