import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import {
  Aws, CfnCondition, CfnOutput, CfnParameter, Fn, Stack, StackProps,
  aws_codebuild as codebuild, aws_iam as iam, aws_logs as logs, aws_stepfunctions as sfn,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const BUILD_IMAGE = 'aws/codebuild/amazonlinux-x86_64-standard:5.0';   // has Python 3 and AWS CLI v2
const MAX_BUILDSPEC = 25600;                                             // CodeBuild's limit for an inline buildspec

/**
 * One CodeBuild project runs collect_inventory.py unchanged and uploads the zip to S3. A one-step
 * Step Functions state machine starts the build and waits for it, so there is no Lambda timeout.
 * The script is embedded in the buildspec, so the template needs no assets, no bootstrap and no Docker.
 */
export class InventoryStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------- parameters
    const bucket = new CfnParameter(this, 'InventoryBucket', {
      type: 'String',
      description: 'S3 bucket that receives the inventory zip',
      allowedPattern: '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$',
      constraintDescription: 'must be an S3 bucket name',
    });
    const prefix = new CfnParameter(this, 'InventoryPrefix', {
      type: 'String',
      default: 'six-pillars-inventory',
      description: 'Key prefix (folder) for the zip in the bucket; may be empty. No leading or trailing slash.',
      allowedPattern: "^([A-Za-z0-9!_.*'()-]+(/[A-Za-z0-9!_.*'()-]+)*)?$",
      constraintDescription: 'letters, digits and !_.*\'()- separated by single slashes, with no leading or trailing slash',
    });
    const roleName = new CfnParameter(this, 'RoleName', {
      type: 'String',
      default: 'six-pillar-diagram-role',
      description: 'IAM role the scan runs as',
      allowedPattern: '^[\\w+=,.@-]{1,64}$',
    });
    const createRole = new CfnParameter(this, 'CreateRole', {
      type: 'String',
      default: 'true',
      allowedValues: ['true', 'false'],
      description: 'true: this stack creates the role. false: the role already exists (its trust policy must allow codebuild.amazonaws.com)',
    });
    const timeout = new CfnParameter(this, 'ScanTimeoutMinutes', {
      type: 'Number',
      default: 120,
      minValue: 5,
      maxValue: 480,
      description: 'Stop the scan if it runs longer than this',
    });
    this.templateOptions.metadata = {
      'AWS::CloudFormation::Interface': {
        ParameterGroups: [
          { Label: { default: 'Where to upload the inventory' }, Parameters: ['InventoryBucket', 'InventoryPrefix'] },
          { Label: { default: 'Scan settings' }, Parameters: ['RoleName', 'CreateRole', 'ScanTimeoutMinutes'] },
        ],
      },
    };

    const shouldCreateRole = new CfnCondition(this, 'ShouldCreateRole', {
      expression: Fn.conditionEquals(createRole.valueAsString, 'true'),
    });

    // ---------------------------------------------------------------- logs
    const logGroup = new logs.CfnLogGroup(this, 'ScanLogs', {
      logGroupName: `/aws/codebuild/${Aws.STACK_NAME}`,
      retentionInDays: 14,
    });

    // ---------------------------------------------------------------- scan role (six-pillar-diagram-role)
    const role = new iam.CfnRole(this, 'ScanRole', {
      roleName: roleName.valueAsString,
      description: '6pillars.ai inventory scan (read-only), used by CodeBuild',
      assumeRolePolicyDocument: {
        Version: '2012-10-17',
        Statement: [{ Effect: 'Allow', Principal: { Service: 'codebuild.amazonaws.com' }, Action: 'sts:AssumeRole' }],
      },
    });
    role.cfnOptions.condition = shouldCreateRole;
    const roleArn = Fn.conditionIf(
      shouldCreateRole.logicalId,
      role.attrArn,
      `arn:${Aws.PARTITION}:iam::${Aws.ACCOUNT_ID}:role/${roleName.valueAsString}`,
    ).toString();
    const roleRef = Fn.conditionIf(shouldCreateRole.logicalId, role.ref, roleName.valueAsString).toString();

    // Read-only permissions come straight from iam-policy.json, so the stack and the
    // script's documentation never disagree. The stack adds only logging and the S3 upload.
    // The policy is attached to the role whether the stack created it or not, and is removed
    // when the stack is deleted.
    const readOnly = JSON.parse(fs.readFileSync(path.join(PROJECT_ROOT, 'iam-policy.json'), 'utf8'));
    const objectArn = `arn:${Aws.PARTITION}:s3:::${bucket.valueAsString}/${prefix.valueAsString}*`;
    const policy = new iam.CfnPolicy(this, 'ScanPolicy', {
      policyName: `${Aws.STACK_NAME}-scan`,
      roles: [roleRef],
      policyDocument: {
        Version: '2012-10-17',
        Statement: [
          ...readOnly.Statement,
          {
            Sid: 'WriteScanLogs',
            Effect: 'Allow',
            Action: ['logs:CreateLogStream', 'logs:PutLogEvents'],
            Resource: `${logGroup.attrArn}`,
          },
          {
            Sid: 'UploadInventoryZip',
            Effect: 'Allow',
            Action: ['s3:PutObject', 's3:PutObjectAcl'],
            Resource: objectArn,
          },
        ],
      },
    });

    // ---------------------------------------------------------------- CodeBuild project
    const payload = execFileSync('python3', [path.join(__dirname, '..', 'scripts', 'pack.py'), PROJECT_ROOT], {
      encoding: 'utf8', maxBuffer: 10 * 1024 * 1024,
    }).trim();

    const script = [
      'set -euo pipefail',
      'mkdir -p /tmp/scanner && cd /tmp/scanner',
      `echo '${payload}' | base64 -d | tar -xJ`,
      'python3 collect_inventory.py --yes --output /tmp/scanner/outputs 2>&1 | tee /tmp/scan.log',
      "ZIP=$(sed -n 's/^RETURN_THIS_ZIP=//p' /tmp/scan.log | tail -n 1)",
      '[ -n "$ZIP" ] && [ -f "$ZIP" ] || { echo "The scan did not produce a zip"; exit 1; }',
      'KEY="${INVENTORY_PREFIX:+$INVENTORY_PREFIX/}$(basename "$ZIP")"',
      'aws s3 cp "$ZIP" "s3://$INVENTORY_BUCKET/$KEY" --acl bucket-owner-full-control --no-progress',
      'export INVENTORY_S3_URI="s3://$INVENTORY_BUCKET/$KEY"',
      'echo "INVENTORY_S3_URI=$INVENTORY_S3_URI"',
    ].join('\n');

    const buildSpec = codebuild.BuildSpec.fromObject({
      version: '0.2',
      env: { shell: 'bash', 'exported-variables': ['INVENTORY_S3_URI'] },
      phases: { build: { commands: [script] } },
    }).toBuildSpec();
    if (buildSpec.length > MAX_BUILDSPEC) {
      throw new Error(`Buildspec is ${buildSpec.length} characters; CodeBuild allows ${MAX_BUILDSPEC}. ` +
        'The script has grown too large to embed.');
    }

    const project = new codebuild.CfnProject(this, 'ScanProject', {
      name: Aws.STACK_NAME,
      description: '6pillars.ai read-only inventory scan',
      serviceRole: roleArn,
      source: { type: 'NO_SOURCE', buildSpec },
      artifacts: { type: 'NO_ARTIFACTS' },
      environment: {
        type: 'LINUX_CONTAINER',
        computeType: 'BUILD_GENERAL1_SMALL',
        image: BUILD_IMAGE,
        environmentVariables: [
          { name: 'INVENTORY_BUCKET', value: bucket.valueAsString },
          { name: 'INVENTORY_PREFIX', value: prefix.valueAsString },
        ],
      },
      timeoutInMinutes: timeout.valueAsNumber,
      concurrentBuildLimit: 1,
      logsConfig: { cloudWatchLogs: { status: 'ENABLED', groupName: logGroup.ref } },
    });
    // CodeBuild checks the role when the project is created, so the policy must be in place first.
    project.addResourceDependency(policy);

    // ---------------------------------------------------------------- Step Functions
    const projectArn = `arn:${Aws.PARTITION}:codebuild:${Aws.REGION}:${Aws.ACCOUNT_ID}:project/${Aws.STACK_NAME}`;
    const machineRole = new iam.CfnRole(this, 'StateMachineRole', {
      description: 'Lets the 6pillars.ai inventory state machine start the scan and wait for it',
      assumeRolePolicyDocument: {
        Version: '2012-10-17',
        Statement: [{ Effect: 'Allow', Principal: { Service: 'states.amazonaws.com' }, Action: 'sts:AssumeRole' }],
      },
      policies: [{
        policyName: 'run-scan',
        policyDocument: {
          Version: '2012-10-17',
          Statement: [
            {
              Effect: 'Allow',
              Action: ['codebuild:StartBuild', 'codebuild:StopBuild', 'codebuild:BatchGetBuilds'],
              Resource: projectArn,
            },
            {
              // Step Functions uses this managed rule to learn when the build finishes.
              Effect: 'Allow',
              Action: ['events:PutTargets', 'events:PutRule', 'events:DescribeRule'],
              Resource: `arn:${Aws.PARTITION}:events:${Aws.REGION}:${Aws.ACCOUNT_ID}:rule/StepFunctionsGetEventForCodeBuildStartBuildRule`,
            },
          ],
        },
      }],
    });

    const definition = {
      Comment: 'Runs the 6pillars.ai inventory scan in CodeBuild and waits for it to finish',
      StartAt: 'RunInventoryScan',
      States: {
        RunInventoryScan: {
          Type: 'Task',
          Resource: 'arn:aws:states:::codebuild:startBuild.sync',
          Parameters: { ProjectName: '${ProjectName}' },
          End: true,
        },
      },
    };
    const machine = new sfn.CfnStateMachine(this, 'ScanStateMachine', {
      stateMachineName: Aws.STACK_NAME,
      roleArn: machineRole.attrArn,
      definitionString: JSON.stringify(definition),
      definitionSubstitutions: { ProjectName: project.ref },
    });

    // ---------------------------------------------------------------- outputs
    new CfnOutput(this, 'StateMachineArn', { value: machine.attrArn });
    new CfnOutput(this, 'ProjectName', { value: project.ref });
    new CfnOutput(this, 'LogGroupName', { value: logGroup.ref });
    new CfnOutput(this, 'InventoryLocation', {
      value: Fn.join('', ['s3://', bucket.valueAsString, '/', prefix.valueAsString]),
    });
    new CfnOutput(this, 'ScanRoleName', { value: roleName.valueAsString });
  }
}
