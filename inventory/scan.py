from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import re

from .catalog import CHECKS, GLOBAL, EXTRA_ARGS, NOT_ENABLED
from .graph import build_graph, listify

ARN = re.compile(r'arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:\d*:[A-Za-z0-9:/_.\-$+=,@]+')
# Subscription endpoints that identify people or external URLs are counted, never stored.
REDACTED_PROTOCOLS = {'email', 'email-json', 'sms', 'http', 'https'}
WORKERS = 8


def run_scan(aws, progress=lambda message, done, total: None, regions=None):
    snapshot = dict(schema=2, started=datetime.now(timezone.utc).isoformat(), demo=False,
                    identity=aws.identity(), records=[], relationships=[], errors=[], coverage=[])
    account = snapshot['identity']['Account']
    if not regions:
        regions = aws.call('ec2', 'describe-regions', query='Regions[].RegionName')
    # Explicit region names only. subprocess always uses argument lists, never a shell.
    if not regions or any(not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', r) for r in regions):
        raise ValueError('Select valid AWS regions.')
    snapshot['regions'] = regions

    def read(service, operation, *args, region=None, query=None):
        try:
            return aws.call(service, operation, *args, region=region, query=query)
        except Exception as exc:
            snapshot['errors'].append(dict(region=region or aws.region, service=service, operation=operation,
                                           message=str(exc)))
            return None

    def parallel(function, items):
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            return list(pool.map(function, items))

    tasks = [(k, s, o, q, r) for r in regions for k, s, o, q in CHECKS]
    tasks += [(k, s, o, q, r) for k, s, o, q, r in GLOBAL]
    progress('Verified identity; collecting regional inventories', 0, len(tasks))

    def collect(task):
        kind, service, operation, query, region = task
        try:
            value = aws.call(service, operation, *EXTRA_ARGS.get(kind, []), region=region, query=query)
            success = True
        except Exception as exc:
            value = None
            # A service that is not switched on in a region is an empty result, not a failed check.
            success = any(marker in str(exc).lower() for marker in NOT_ENABLED)
            if not success:
                snapshot['errors'].append(dict(region=region, service=service, operation=operation, message=str(exc)))
        resources = listify(value)
        if kind in ('SNS', 'SQS'):
            resources = [{'id': r, 'name': r.split(':' if kind == 'SNS' else '/')[-1],
                          'arn': r if kind == 'SNS' else f'arn:aws:sqs:{region}:{account}:{r.split("/")[-1]}'}
                         for r in resources]
        scope = 'global' if (kind, service, operation, query, region) in GLOBAL else region
        return dict(kind=kind, region=scope, resources=resources, success=success)

    # Completion/error is determined using per-call failures, not empty-result truthiness.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(collect, task) for task in tasks]
        for done, future in enumerate(as_completed(futures), 1):
            snapshot['records'].append(future.result())
            progress('Inventory collection', done, len(tasks))

    def resources(kind):
        return [(r['region'], p) for r in snapshot['records'] if r['kind'] == kind for p in r['resources']]

    def relation(source, targets, label, evidence, region, kind='connection', types=None):
        item = dict(source=source, targets=listify(targets), label=label, evidence=evidence, region=region, kind=kind)
        if types is not None:
            item['types'] = types
        snapshot['relationships'].append(item)

    total = len(tasks)
    progress('Tracing stack ownership and integrations', total, total)

    def stack_resources(item):
        region, p = item
        if p.get('state') == 'DELETE_COMPLETE':
            return None
        value = read('cloudformation', 'list-stack-resources', '--stack-name', p['name'], region=region,
                     query='StackResourceSummaries[].{type:ResourceType,id:PhysicalResourceId}')
        # IAM roles and policies owned by the stack are left out: this version reads no IAM data.
        return region, p, [x for x in listify(value) if x.get('id') and not (x.get('type') or '').startswith('AWS::IAM::')]
    for result in parallel(stack_resources, resources('CloudFormation')):
        if result:
            region, p, owned = result
            relation(p['id'], [x['id'] for x in owned], 'owns', 'CloudFormation resources', region, 'ownership',
                     [x.get('type') for x in owned])

    for region, p in resources('API Gateway'):
        value = read('apigateway', 'get-resources', '--rest-api-id', p['id'], region=region,
                     query='items[].{id:id,path:path,methods:resourceMethods}')
        for endpoint in listify(value):
            for method in (endpoint.get('methods') or {}):
                integration = read('apigateway', 'get-integration', '--rest-api-id', p['id'], '--resource-id',
                                   endpoint['id'], '--http-method', method, region=region, query='{type:type,uri:uri}')
                if integration and ':function:' in (integration.get('uri') or ''):
                    match = re.search(r'(arn:[^/]+:function:[^/]+)', integration['uri'])
                    if match:
                        relation(p['id'], [match.group(1)], 'invokes Lambda', 'API integration ' + method + ' ' + endpoint['path'], region)
    for region, p in resources('HTTP/WebSocket API'):
        value = read('apigatewayv2', 'get-integrations', '--api-id', p['id'], region=region,
                     query='Items[].{type:IntegrationType,uri:IntegrationUri}')
        for integration in listify(value):
            match = re.search(r'(arn:[^/]+:function:[^/]+)', integration.get('uri') or '')
            if match:
                relation(p['id'], [match.group(1)], 'invokes Lambda', 'API v2 integration', region)

    # WAF web ACLs: which APIs, load balancers and AppSync APIs each one protects.
    def waf_targets(item):
        region, p = item
        found = []
        for rtype in ('API_GATEWAY', 'APPLICATION_LOAD_BALANCER', 'APPSYNC'):
            found += listify(read('wafv2', 'list-resources-for-web-acl', '--web-acl-arn', p['id'], '--resource-type',
                                  rtype, region=region, query='ResourceArns'))
        return region, p, found
    for region, p, found in parallel(waf_targets, resources('WAF web ACL')):
        relation(p['id'], found, 'protects', 'WAF web ACL association', region)
    for region, p in resources('CloudFront'):
        if p.get('webacl'):
            relation(p['webacl'], [p['id']], 'protects', 'CloudFront web ACL', region)

    # CodePipeline: which CodeBuild projects a pipeline runs and which stacks it deploys.
    def pipeline_stages(item):
        region, p = item
        return region, p, read('codepipeline', 'get-pipeline', '--name', p['id'], region=region,
                               query='pipeline.stages[].actions[].{provider:actionTypeId.provider,'
                                     'project:configuration.ProjectName,stack:configuration.StackName}')
    for region, p, actions in parallel(pipeline_stages, resources('CodePipeline')):
        actions = listify(actions)
        relation(p['id'], [a['project'] for a in actions if a.get('project')], 'runs build', 'CodePipeline stage', region)
        relation(p['id'], [a['stack'] for a in actions if a.get('stack')], 'deploys', 'CodePipeline stage', region)

    # Client VPN: the subnets an endpoint is associated with.
    def vpn_networks(item):
        region, p = item
        p['subnets'] = listify(read('ec2', 'describe-client-vpn-target-networks', '--client-vpn-endpoint-id', p['id'],
                                    region=region, query='ClientVpnTargetNetworks[].TargetNetworkId'))
    parallel(vpn_networks, resources('Client VPN endpoint'))

    # KMS: names from aliases; AWS managed keys are marked.
    def key_aliases(region):
        return region, read('kms', 'list-aliases', region=region, query='Aliases[?TargetKeyId].{alias:AliasName,key:TargetKeyId}')
    aliases = {}
    for region, found in parallel(key_aliases, sorted({r for r, _ in resources('KMS key')})):
        for a in listify(found):
            aliases[(region, a['key'])] = a['alias']
    for region, p in resources('KMS key'):
        alias = aliases.get((region, p['name']))
        if alias:
            p['name'], p['managed'] = alias.replace('alias/', '', 1), alias.startswith('alias/aws/')

    # Public network interfaces: which resource owns each one (its cost belongs to that resource).
    for region, p in resources('Network interface'):
        text = str(p.get('description') or '')
        owner = re.search(r'(nat-[0-9a-f]+|cvpn-endpoint-[0-9a-f]+)', text)
        elb = re.search(r'ELB ((?:app|net|gwy)/[^ ]+)', text)
        p['owner'] = owner.group(1) if owner else p.get('attached') or (elb.group(1) if elb else None)
        if p['owner']:
            relation(p['id'], [p['owner']], 'interface of', 'Network interface owner', region)

    def rule_targets(item):
        region, p = item
        return region, p, read('events', 'list-targets-by-rule', '--rule', p['name'], region=region,
                               query='Targets[].{arn:Arn}')
    for region, p, value in parallel(rule_targets, resources('EventBridge')):
        targets = listify(value)
        relation(p['id'], [t['arn'] for t in targets if t.get('arn')], 'target: ' + (p.get('schedule') or 'event match'),
                 'EventBridge configured targets', region)

    # CloudTrail health: whether the trail is logging and the latest delivery error (if any).
    def trail_status(item):
        region, p = item
        return p, read('cloudtrail', 'get-trail-status', '--name', p['id'], region=region,
                       query='{logging:IsLogging,error:LatestDeliveryError,last:LatestDeliveryTime}')
    for p, value in parallel(trail_status, resources('CloudTrail')):
        if value:
            p['logging'], p['delivery_error'], p['last_delivery'] = value.get('logging'), value.get('error'), value.get('last')

    # Step Functions: only the ARNs referenced by the definition are kept, never the definition itself.
    def state_machine(item):
        region, p = item
        return region, p, read('stepfunctions', 'describe-state-machine', '--state-machine-arn', p['id'],
                               region=region, query='{definition:definition,status:status}')
    for region, p, value in parallel(state_machine, resources('Step Functions')):
        if value:
            p['state'] = value.get('status')
            targets = sorted({a for a in ARN.findall(value.get('definition') or '') if ':states:::' not in a})
            relation(p['id'], targets, 'invokes', 'Step Functions definition', region)

    # SNS subscriptions are read per topic: ListSubscriptions omits subscriptions created by other accounts
    # (for example a SaaS subscribing its own queue), ListSubscriptionsByTopic returns them to the topic owner.
    def topic_subscriptions(item):
        region, p = item
        return region, p, read('sns', 'list-subscriptions-by-topic', '--topic-arn', p['id'], region=region,
                               query='Subscriptions[].{protocol:Protocol,endpoint:Endpoint}')
    for region, topic, subs in parallel(topic_subscriptions, resources('SNS')):
        for sub in listify(subs):
            topic['subscriptions'] = topic.get('subscriptions', 0) + 1
            if sub.get('protocol') in REDACTED_PROTOCOLS or not str(sub.get('endpoint', '')).startswith('arn:'):
                topic['redacted_subscriptions'] = topic.get('redacted_subscriptions', 0) + 1
                continue
            relation(topic['id'], [sub['endpoint']], 'subscription (' + str(sub.get('protocol')) + ')',
                     'SNS subscription', region)

    # Lambda event sources (SQS, Kinesis, DynamoDB streams): one paginated call per region.
    def event_sources(region):
        return region, read('lambda', 'list-event-source-mappings', region=region,
                            query='EventSourceMappings[].{source:EventSourceArn,fn:FunctionArn,state:State}')
    for region, maps in parallel(event_sources, regions):
        for mapping in listify(maps):
            if mapping.get('source') and mapping.get('fn'):
                function = mapping['fn'] if mapping['fn'].count(':') <= 6 else ':'.join(mapping['fn'].split(':')[:7])
                relation(mapping['source'], [function], 'event source (' + str(mapping.get('state')) + ')',
                         'Lambda event source mapping', region)

    def bucket_region(bucket):
        region = read('s3api', 'get-bucket-location', '--bucket', bucket['id'], query='LocationConstraint')
        bucket['region'] = 'eu-west-1' if region == 'EU' else region or 'us-east-1'
    parallel(bucket_region, [b for record in snapshot['records'] if record['kind'] == 'S3' for b in record['resources']])

    progress('Cross-checking tagged resources and existing Resource Explorer indexes', total, total)
    snapshot['discovery'] = []
    for region in regions:
        value = read('resourcegroupstaggingapi', 'get-resources', region=region,
                     query="ResourceTagMappingList[?!starts_with(ResourceARN, 'arn:aws:payments:')].{arn:ResourceARN,tags:Tags[?Key==`Project` || Key==`Name` || Key==`Application` || Key==`Environment`]}")
        snapshot['discovery'].append(dict(source='Tagging API', region=region, resources=listify(value)))
    indexes = read('resource-explorer-2', 'list-indexes', query='Indexes[].{region:Region,type:Type}')
    for index in listify(indexes):
        if index['region'] not in regions:
            continue
        value = read('resource-explorer-2', 'search', '--query-string', '*', region=index['region'],
                     query='{count:Count,resources:Resources[].{arn:Arn,type:ResourceType,region:Region}}')
        snapshot['discovery'].append(dict(source='Resource Explorer', region=index['region'], result=value))
    # Apply Project tags to already-discovered resources without converting defaults into active apps.
    for discovered in snapshot['discovery']:
        if discovered['source'] != 'Tagging API':
            continue
        for resource in discovered['resources']:
            project = next((t['Value'] for t in resource.get('tags', []) if t['Key'] == 'Project'), None)
            if not project:
                continue
            arn = resource['arn']
            native = arn.split('/')[-1] if ':s3:::' not in arn else arn.split(':::')[-1]
            for record in snapshot['records']:
                if record['region'] not in (discovered['region'], 'global'):
                    continue
                for p in record['resources']:
                    if isinstance(p, dict) and str(p.get('id')) in (arn, native) or \
                            isinstance(p, dict) and p.get('arn') == arn:
                        p['project'] = project
    snapshot['ses_account'] = read('sesv2', 'get-account', query='{sandbox:!ProductionAccessEnabled,sending:SendingEnabled}')
    snapshot['graph'] = build_graph(snapshot['records'], snapshot['relationships'], account)
    snapshot['coverage'] = [dict(region=r['region'], kind=r['kind'], resources=len(r['resources']), success=r['success']) for r in snapshot['records']]
    snapshot['completed'] = datetime.now(timezone.utc).isoformat()
    snapshot['calls'] = aws.calls
    return snapshot
