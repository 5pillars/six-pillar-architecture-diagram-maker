#!/usr/bin/env node
import { App, DefaultStackSynthesizer } from 'aws-cdk-lib';
import { InventoryStack } from '../lib/inventory-stack';

const app = new App();

// The template must deploy with plain `aws cloudformation deploy` in CloudShell, so it uses no
// assets and no bootstrap resources. The account and Region come from where it is deployed.
new InventoryStack(app, 'SixPillarsInventory', {
  stackName: 'six-pillars-inventory',
  description: '6pillars.ai read-only inventory scan: Step Functions runs the scan in CodeBuild and uploads the zip to S3',
  synthesizer: new DefaultStackSynthesizer({ generateBootstrapVersionRule: false }),
  analyticsReporting: false,
});
