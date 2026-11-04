# All projections intentionally omit credentials, Lambda environment values and object contents.
# (kind, service, operation, JMESPath returning a list of resources)
CHECKS = [
 ('EC2','ec2','describe-instances',"Reservations[].Instances[].{id:InstanceId,name:Tags[?Key==`Name`].Value|[0],project:Tags[?Key==`Project`].Value|[0],state:State.Name,type:InstanceType,os:Platform,usage:UsageOperation,vpc:VpcId,subnet:SubnetId,groups:SecurityGroups[].GroupId,volumes:BlockDeviceMappings[].Ebs.VolumeId,ip:PublicIpAddress}"),
 ('EBS','ec2','describe-volumes','Volumes[].{id:VolumeId,state:State,size:Size,type:VolumeType,iops:Iops,throughput:Throughput,encrypted:Encrypted,instances:Attachments[].InstanceId}'),
 ('EBS snapshot','ec2','describe-snapshots','Snapshots[].{id:SnapshotId,state:State,size:VolumeSize,volume:VolumeId}'),
 ('Elastic IP','ec2','describe-addresses','Addresses[].{id:AllocationId,name:PublicIp,instance:InstanceId}'),
 ('VPC','ec2','describe-vpcs','Vpcs[].{id:VpcId,default:IsDefault,cidr:CidrBlock,name:Tags[?Key==`Name`].Value|[0]}'),
 ('Subnet','ec2','describe-subnets','Subnets[].{id:SubnetId,vpc:VpcId,az:AvailabilityZone,publicIP:MapPublicIpOnLaunch,cidr:CidrBlock,name:Tags[?Key==`Name`].Value|[0]}'),
 ('Internet Gateway','ec2','describe-internet-gateways','InternetGateways[].{id:InternetGatewayId,vpcs:Attachments[].VpcId}'),
 ('Route table','ec2','describe-route-tables','RouteTables[].{id:RouteTableId,vpc:VpcId,routes:Routes[].{destination:DestinationCidrBlock,gateway:GatewayId,nat:NatGatewayId},assoc:Associations[].{subnet:SubnetId,main:Main}}'),
 ('Security group','ec2','describe-security-groups','SecurityGroups[].{id:GroupId,name:GroupName,vpc:VpcId,ingress:IpPermissions[].{from:FromPort,to:ToPort,protocol:IpProtocol,sourceCount:length(IpRanges)}}'),
 ('NAT','ec2','describe-nat-gateways','NatGateways[].{id:NatGatewayId,state:State,vpc:VpcId,subnet:SubnetId}'),
 ('VPC endpoint','ec2','describe-vpc-endpoints','VpcEndpoints[].{id:VpcEndpointId,name:ServiceName,type:VpcEndpointType,vpc:VpcId}'),
 ('Load balancer','elbv2','describe-load-balancers','LoadBalancers[].{id:LoadBalancerArn,name:LoadBalancerName,state:State.Code,vpc:VpcId,subnets:AvailabilityZones[].SubnetId,scheme:Scheme,type:Type}'),
 ('RDS','rds','describe-db-instances','DBInstances[].{id:DBInstanceIdentifier,name:DBInstanceIdentifier,state:DBInstanceStatus,type:DBInstanceClass,engine:Engine,cluster:DBClusterIdentifier,az:AvailabilityZone,vpc:DBSubnetGroup.VpcId,subnets:DBSubnetGroup.Subnets[].SubnetIdentifier,resource_id:DbiResourceId}'),
 ('RDS cluster','rds','describe-db-clusters','DBClusters[].{id:DBClusterIdentifier,state:Status,engine:Engine,resource_id:DbClusterResourceId}'),
 ('Lambda','lambda','list-functions','Functions[].{id:FunctionArn,name:FunctionName,runtime:Runtime,package:PackageType,memory:MemorySize,timeout:Timeout,vpc:VpcConfig.VpcId,subnets:VpcConfig.SubnetIds}'),
 ('Step Functions','stepfunctions','list-state-machines','stateMachines[].{id:stateMachineArn,name:name,type:type}'),
 ('API Gateway','apigateway','get-rest-apis','items[].{id:id,name:name}'),
 ('HTTP/WebSocket API','apigatewayv2','get-apis','Items[].{id:ApiId,name:Name,type:ProtocolType}'),
 ('DynamoDB','dynamodb','list-tables','TableNames'),
 ('EventBridge','events','list-rules','Rules[].{id:Arn,name:Name,state:State,schedule:ScheduleExpression}'),
 ('ECS','ecs','list-clusters','clusterArns'),
 ('EKS','eks','list-clusters','clusters'),
 ('ECR','ecr','describe-repositories','repositories[].{id:repositoryArn,name:repositoryName}'),
 ('SageMaker endpoint','sagemaker','list-endpoints','Endpoints[].{id:EndpointArn,name:EndpointName,state:EndpointStatus}'),
 ('OpenSearch','opensearch','list-domain-names','DomainNames[].DomainName'),
 ('SNS','sns','list-topics','Topics[].TopicArn'),
 ('SQS','sqs','list-queues','QueueUrls'),
 ('CloudWatch alarm','cloudwatch','describe-alarms','MetricAlarms[].{id:AlarmArn,name:AlarmName,state:StateValue,namespace:Namespace,metric:MetricName,dimensions:Dimensions,actions:AlarmActions}'),
 ('CloudWatch logs','logs','describe-log-groups','logGroups[].{id:logGroupName,name:logGroupName,bytes:storedBytes,retention:retentionInDays}'),
 ('CloudFormation','cloudformation','describe-stacks','Stacks[].{id:StackId,name:StackName,state:StackStatus}'),
 ('EFS','efs','describe-file-systems','FileSystems[].{id:FileSystemId,bytes:SizeInBytes.Value}'),
 ('ElastiCache','elasticache','describe-cache-clusters','CacheClusters[].{id:CacheClusterId,state:CacheClusterStatus,type:CacheNodeType,az:PreferredAvailabilityZone}'),
 ('ElastiCache Serverless','elasticache','describe-serverless-caches','ServerlessCaches[].{id:ARN,name:ServerlessCacheName,state:Status,type:Engine,subnets:SubnetIds,groups:SecurityGroupIds}'),
 # Only interfaces with a public IPv4 address: they are billed, and show where the account is reachable.
 ('Network interface','ec2','describe-network-interfaces','NetworkInterfaces[?Association.PublicIp].{id:NetworkInterfaceId,name:NetworkInterfaceId,subnet:SubnetId,vpc:VpcId,az:AvailabilityZone,type:InterfaceType,description:Description,attached:Attachment.InstanceId,publicIp:Association.PublicIp}'),
 ('Client VPN endpoint','ec2','describe-client-vpn-endpoints','ClientVpnEndpoints[].{id:ClientVpnEndpointId,name:Tags[?Key==`Name`].Value|[0],state:Status.Code,vpc:VpcId,cidr:ClientCidrBlock}'),
 ('AppSync API','appsync','list-graphql-apis','graphqlApis[].{id:arn,name:name,type:apiType}'),
 ('Bedrock guardrail','bedrock','list-guardrails','guardrails[].{id:arn,name:name,state:status}'),
 ('WAF web ACL','wafv2','list-web-acls','WebACLs[].{id:ARN,name:Name}'),
 ('KMS key','kms','list-keys','Keys[].{id:KeyArn,name:KeyId}'),
 ('Secrets Manager secret','secretsmanager','list-secrets','SecretList[].{id:ARN,name:Name}'),
 ('CodeBuild project','codebuild','list-projects','projects'),
 ('CodePipeline','codepipeline','list-pipelines','pipelines[].{id:name,name:name}'),
 ('Resilience Hub app','resiliencehub','list-apps','appSummaries[].{id:appArn,name:name,state:status}'),
 ('Macie job','macie2','list-classification-jobs','items[].{id:jobId,name:name,state:jobStatus}'),
 ('GuardDuty detector','guardduty','list-detectors','DetectorIds'),
 ('Redshift','redshift','describe-clusters','Clusters[].{id:ClusterIdentifier,state:ClusterStatus,type:NodeType}'),
 ('SES identity','sesv2','list-email-identities','EmailIdentities[].{id:IdentityName,name:IdentityName,state:VerificationStatus}'),
 ('App Runner','apprunner','list-services','ServiceSummaryList[].{id:ServiceArn,name:ServiceName,state:Status}'),
 ('CloudTrail','cloudtrail','describe-trails','trailList[].{id:TrailARN,name:Name,bucket:S3BucketName,logs:CloudWatchLogsLogGroupArn,multi:IsMultiRegionTrail,home:HomeRegion}'),
 ('Config','configservice','describe-delivery-channels','DeliveryChannels[].{id:name,name:name,bucket:s3BucketName,topic:snsTopicARN}'),
 ('Security Hub','securityhub','describe-hub','{id:HubArn,name:`Security Hub`,since:SubscribedAt}'),
]

# Extra fixed arguments for some inventory calls.
EXTRA_ARGS = {'EBS snapshot':['--owner-ids','self'], 'CloudTrail':['--no-include-shadow-trails'],
              'WAF web ACL':['--scope','REGIONAL'], 'WAF web ACL (CloudFront)':['--scope','CLOUDFRONT']}

# A service that is not switched on in a Region answers with an error; that is an empty result, not a failed call.
NOT_ENABLED = ('not subscribed', 'is not enabled', 'macie is not enabled', 'not been enabled', 'opted in')

GLOBAL = [
 ('S3','s3api','list-buckets','Buckets[].{id:Name,name:Name}','us-east-1'),
 ('CloudFront','cloudfront','list-distributions','DistributionList.Items[].{id:ARN,name:DomainName,state:Enabled,origins:Origins.Items[].DomainName,webacl:WebACLId}','us-east-1'),
 ('WAF web ACL (CloudFront)','wafv2','list-web-acls','WebACLs[].{id:ARN,name:Name}','us-east-1'),
 ('Route 53','route53','list-hosted-zones','HostedZones[].{id:Id,name:Name}','us-east-1'),
 ('Registered domain','route53domains','list-domains','Domains[].{id:DomainName,name:DomainName}','us-east-1'),
 ('Global Accelerator','globalaccelerator','list-accelerators','Accelerators[].{id:AcceleratorArn,name:Name,state:Status}','us-west-2'),
]
