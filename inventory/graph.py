import re


def listify(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


# CloudFormation resource type -> collector kind, used to disambiguate same-named resources.
CFN_KINDS = {'AWS::IAM::Role':'IAM role','AWS::IAM::InstanceProfile':'Instance profile','AWS::Lambda::Function':'Lambda',
             'AWS::Logs::LogGroup':'CloudWatch logs','AWS::S3::Bucket':'S3','AWS::DynamoDB::Table':'DynamoDB',
             'AWS::EC2::Instance':'EC2','AWS::EC2::Volume':'EBS','AWS::EC2::SecurityGroup':'Security group',
             'AWS::EC2::VPC':'VPC','AWS::EC2::Subnet':'Subnet','AWS::EC2::InternetGateway':'Internet Gateway',
             'AWS::EC2::RouteTable':'Route table','AWS::EC2::NatGateway':'NAT','AWS::EC2::VPCEndpoint':'VPC endpoint',
             'AWS::EC2::EIP':'Elastic IP','AWS::SNS::Topic':'SNS','AWS::SQS::Queue':'SQS','AWS::Events::Rule':'EventBridge',
             'AWS::CloudFormation::Stack':'CloudFormation','AWS::ECR::Repository':'ECR','AWS::ApiGateway::RestApi':'API Gateway',
             'AWS::ApiGatewayV2::Api':'HTTP/WebSocket API','AWS::CloudWatch::Alarm':'CloudWatch alarm',
             'AWS::ElasticLoadBalancingV2::LoadBalancer':'Load balancer','AWS::RDS::DBInstance':'RDS',
             'AWS::RDS::DBCluster':'RDS cluster','AWS::ECS::Cluster':'ECS','AWS::EKS::Cluster':'EKS',
             'AWS::CloudFront::Distribution':'CloudFront','AWS::Route53::HostedZone':'Route 53',
             'AWS::ElastiCache::ServerlessCache':'ElastiCache Serverless','AWS::AppSync::GraphQLApi':'AppSync API',
             'AWS::Bedrock::Guardrail':'Bedrock guardrail','AWS::WAFv2::WebACL':'WAF web ACL','AWS::KMS::Key':'KMS key',
             'AWS::SecretsManager::Secret':'Secrets Manager secret','AWS::CodeBuild::Project':'CodeBuild project',
             'AWS::CodePipeline::Pipeline':'CodePipeline','AWS::EC2::ClientVpnEndpoint':'Client VPN endpoint',
             'AWS::GuardDuty::Detector':'GuardDuty detector','AWS::StepFunctions::StateMachine':'Step Functions'}


def short_name(value):
    # ARNs without a '/' (SNS topics, some Lambda forms) otherwise show the whole ARN as the label.
    text = str(value)
    return text.split('/')[-1] if '/' in text else text.split(':')[-1]


def infer_project(name):
    # Neutral handoff: use Project tags and stack/deployment ownership, not personal naming shortcuts.
    return None


def build_graph(records, relationships, account=None):
    nodes, index = [], {}
    for record in records:
        for raw in listify(record.get('resources')):
            props = raw if isinstance(raw, dict) else {'id':raw, 'name':short_name(raw)}
            if not props.get('id'):
                continue
            native = str(props['id'])
            region = record['region']
            kind = record['kind']
            key = f'{region}|{kind}|{native}'
            if key in index:
                continue
            label = str(props.get('name') or short_name(native))
            tag_project=props.get('project')
            project = (infer_project(tag_project) or tag_project) if tag_project else infer_project(label)
            basis = 'Project tag' if props.get('project') else 'Name inference' if project else 'Unassigned'
            node = dict(id=key, native=native, label=label, kind=kind, region=region,
                        project=project or 'Shared / unassigned', basis=basis, properties=props)
            index[key] = node
            nodes.append(node)
    # Exact IDs are matched before names: names (e.g. a role and a function called the same) are ambiguous.
    by_id, by_name = {}, {}
    for node in nodes:
        by_id.setdefault(node['native'], []).append(node)
        if node['properties'].get('arn') and node['properties']['arn'] != node['native']:
            by_id.setdefault(node['properties']['arn'], []).append(node)
        by_name.setdefault(node['label'], []).append(node)
    def find(native, region=None, kind=None):
        for table in (by_id, by_name):
            match = next((n for n in table.get(str(native), [])
                          if (not region or n['region'] in (region,'global')) and (not kind or n['kind']==kind)), None)
            if match:
                return match
        return None
    # Stack membership is stronger evidence than name-based grouping. Nested stacks take their root
    # stack's project regardless of the order stacks were listed in.
    ownership = [r for r in relationships if r['kind'] == 'ownership']
    parent = {}
    for relation in ownership:
        owner = find(relation['source'], relation.get('region'), 'CloudFormation')
        if not owner:
            continue
        types = relation.get('types') or [None]*len(relation['targets'])
        for native, rtype in zip(relation['targets'], types):
            child = find(native, relation.get('region'), CFN_KINDS.get(rtype))
            if child and child['kind'] == 'CloudFormation' and child['id'] != owner['id']:
                parent[child['id']] = owner
    def root(stack):
        seen = set()
        while stack['id'] in parent and stack['id'] not in seen:
            seen.add(stack['id'])
            stack = parent[stack['id']]
        return stack
    for relation in ownership:
        owner = find(relation['source'], relation.get('region'), 'CloudFormation')
        if owner:
            top = root(owner)
            project = infer_project(top['label']) or top['label']
            owner.update(project=project, basis='CloudFormation stack' if top is owner else 'CloudFormation ownership')
            types = relation.get('types') or [None]*len(relation['targets'])
            for native, rtype in zip(relation['targets'], types):
                child = find(native, relation.get('region'), CFN_KINDS.get(rtype))
                if child and child['kind'] != 'CloudFormation':
                    child.update(project=project, basis='CloudFormation ownership')
    for node in nodes:
        if node['basis']=='Unassigned' and node['kind'] in ('EC2','Lambda','RDS','ECS','EKS','App Runner'):
            node.update(project=node['label'],basis='Standalone deployment resource')
    edges = []
    seen = set()
    def external(owner):
        # Another AWS account referenced by this one; it is never scanned.
        key = f'external|External account|{owner}'
        if key not in index:
            node = dict(id=key, native=owner, label='Account '+owner, kind='External account', region='external',
                        project='Outside this account', basis='Referenced by configuration', properties={'id':owner})
            index[key] = node
            nodes.append(node)
        return index[key]
    def resolve(value, region):
        value = str(value)
        if ':apigateway:' in value and '/restapis/' in value:   # WAF names a REST API by its stage ARN
            value = value.split('/restapis/')[1].split('/')[0]
        elif re.match(r'^(app|net|gwy)/', value):   # a load balancer named the way its network interfaces name it
            return next((n for n in nodes if str(n['native']).endswith('loadbalancer/' + value)), None)
        elif ':log-group:' in value:
            value = value.split(':log-group:')[1].split(':')[0]
        elif value.startswith('arn:aws:s3:::'):
            value = value.split(':::')[1].split('/')[0]
        return find(value, region)
    def connect(source, target, label, evidence, region=None):
        a, b = resolve(source, region), resolve(target, region)
        detail = None
        if a and not b and account:
            parts = str(target).split(':')
            if str(target).startswith('arn:') and len(parts) > 5 and parts[4] not in ('', account):
                b, detail = external(parts[4]), short_name(target)
        if not a or not b or a['id'] == b['id']:
            return
        signature = (a['id'],b['id'],label)
        if signature in seen:
            return
        seen.add(signature)
        edges.append(dict(source=a['id'],target=b['id'],label=label,evidence=evidence))
        if detail:
            edges[-1]['detail'] = detail
        if label == 'attached disk' and b['basis'] == 'Unassigned' and a['project'] != 'Shared / unassigned':
            b.update(project=a['project'],basis='EC2 attachment')
        if label == 'assigned address' and a['basis'] == 'Unassigned' and b['project'] != 'Shared / unassigned':
            a.update(project=b['project'],basis='EC2 association')
    for node in nodes:
        p, native, r = node['properties'],node['native'],node['region']
        for volume in p.get('volumes') or []:
            connect(native,volume,'attached disk','EC2 block-device mapping',r)
        if p.get('instance'):
            connect(native,p['instance'],'assigned address','Elastic IP association',r)
        if p.get('subnet'):
            connect(native,p['subnet'],'subnet','AWS subnet reference',r)
        if p.get('vpc'):
            connect(native,p['vpc'],'VPC','AWS VPC reference',r)
        for group in p.get('groups') or []:
            connect(native,group,'security group','EC2 security-group reference',r)
        for dimension in p.get('dimensions') or []:
            connect(native,dimension.get('Value'),'monitors','CloudWatch metric dimension',r)
        for action in p.get('actions') or []:
            connect(native,action,'notifies','CloudWatch alarm action',r)
        if p.get('bucket'):
            connect(native,p['bucket'],'delivers to','Configured delivery bucket',r)
        if p.get('topic'):
            connect(native,p['topic'],'notifies','Configured SNS topic',r)
        if p.get('logs'):
            connect(native,p['logs'],'delivers to','Configured CloudWatch Logs group',r)
    for relation in relationships:
        for target in relation['targets']:
            connect(relation['source'], target, relation['label'], relation['evidence'],relation.get('region'))
    return dict(nodes=nodes,edges=edges)
