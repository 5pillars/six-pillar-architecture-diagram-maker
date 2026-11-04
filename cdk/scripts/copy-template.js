// Copies the synthesized template to ../cloudformation so it can be deployed without CDK.
const fs = require('fs');
const path = require('path');

const source = path.join(__dirname, '..', 'cdk.out', 'six-pillars-inventory.template.json');
const target = path.join(__dirname, '..', '..', 'cloudformation', 'six-pillars-inventory.template.json');
const altSource = path.join(__dirname, '..', 'cdk.out', 'SixPillarsInventory.template.json');

const from = fs.existsSync(source) ? source : altSource;
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.copyFileSync(from, target);
const size = fs.statSync(target).size;
console.log(`Wrote ${path.relative(process.cwd(), target)} (${size} bytes)`);
if (size > 51200) {
  console.error('Template is over 51,200 bytes: aws cloudformation deploy will need --s3-bucket.');
  process.exit(1);
}
