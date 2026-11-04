"""CloudFormation custom resource that starts the inventory scan.

Create, or Update with a new RunId: start the state machine.
Delete: stop any scan still running, so the stack deletes cleanly.
"""
import json
import time
import urllib.request

import boto3

sfn = boto3.client('stepfunctions')


def reply(event, context, status, reason='', data=None):
    body = json.dumps({
        'Status': status,
        'Reason': (reason or 'Log stream: ' + context.log_stream_name)[:900],
        'PhysicalResourceId': event.get('PhysicalResourceId') or 'scan-starter',
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {},
    }).encode()
    request = urllib.request.Request(event['ResponseURL'], data=body, method='PUT',
                                     headers={'Content-Type': ''})
    urllib.request.urlopen(request, timeout=30)


def handler(event, context):
    kind = event['RequestType']
    machine = event['ResourceProperties']['StateMachineArn']
    print('Request:', kind, machine)
    try:
        data = {}
        if kind in ('Create', 'Update'):
            name = 'scan-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + event['RequestId'][:8]
            data['ExecutionArn'] = sfn.start_execution(stateMachineArn=machine, name=name)['executionArn']
            print('Started', data['ExecutionArn'])
        else:
            pages = sfn.get_paginator('list_executions').paginate(stateMachineArn=machine, statusFilter='RUNNING')
            for page in pages:
                for execution in page['executions']:
                    sfn.stop_execution(executionArn=execution['executionArn'], cause='Stack deleted')
                    print('Stopped', execution['executionArn'])
        reply(event, context, 'SUCCESS', data=data)
    except Exception as exc:
        print('Failed:', exc)
        # Never block a stack delete; fail a create or update so the reason shows in CloudFormation.
        reply(event, context, 'SUCCESS' if kind == 'Delete' else 'FAILED', str(exc))
