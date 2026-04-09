"""AWS Step Functions event facet handlers.

Handles CreateStateMachine, StartExecution, DescribeExecution,
DeleteStateMachine, and ListExecutions event facets from the
aws.stepfunctions namespace. Each handler makes real boto3 calls to LocalStack.
"""

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3

log = logging.getLogger(__name__)

NAMESPACE = "aws.stepfunctions"

LOCALSTACK_URL = os.environ.get("LOCALSTACK_URL", "http://localhost:4566")


def _sfn_client():
    """Create a boto3 Step Functions client pointing at LocalStack."""
    return boto3.client(
        "stepfunctions",
        endpoint_url=LOCALSTACK_URL,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def _iam_client():
    """Create a boto3 IAM client pointing at LocalStack."""
    return boto3.client(
        "iam",
        endpoint_url=LOCALSTACK_URL,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def _ensure_sfn_role() -> str:
    """Ensure a Step Functions execution role exists in LocalStack."""
    iam = _iam_client()
    role_name = "afl-stepfunctions-role"
    try:
        resp = iam.get_role(RoleName=role_name)
        return resp["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust_policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "states.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        )
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Path="/",
        )
        return resp["Role"]["Arn"]


def _default_definition() -> str:
    """Build a simple Pass-state ASL definition."""
    return json.dumps(
        {
            "Comment": "AFL default state machine",
            "StartAt": "PassState",
            "States": {
                "PassState": {
                    "Type": "Pass",
                    "Result": {"message": "Hello from FFL Step Functions"},
                    "End": True,
                },
            },
        }
    )


def _create_state_machine_handler(payload: dict) -> dict[str, Any]:
    """Create a Step Functions state machine."""
    step_log = payload.get("_step_log")
    client = _sfn_client()
    role_arn = _ensure_sfn_role()

    sm_name = payload.get("state_machine_name", "afl-state-machine")
    if step_log:
        step_log(f"CreateStateMachine: {sm_name}")
    definition = payload.get("definition", "") or _default_definition()
    custom_role = payload.get("role_arn", "")

    resp = client.create_state_machine(
        name=sm_name,
        definition=definition,
        roleArn=custom_role or role_arn,
    )

    return {
        "config": {
            "state_machine_arn": resp["stateMachineArn"],
            "state_machine_name": sm_name,
            "definition": definition,
            "role_arn": custom_role or role_arn,
            "creation_date": resp.get("creationDate", datetime.now(UTC)).isoformat()
            if isinstance(resp.get("creationDate"), datetime)
            else str(resp.get("creationDate", datetime.now(UTC).isoformat())),
            "status": "ACTIVE",
        },
    }


def _start_execution_handler(payload: dict) -> dict[str, Any]:
    """Start execution of a state machine."""
    step_log = payload.get("_step_log")
    client = _sfn_client()
    sm_arn = payload.get("state_machine_arn", "")
    if step_log:
        step_log(f"StartExecution: {sm_arn}")
    input_payload = payload.get("input_payload", "{}")
    exec_name = payload.get("execution_name", "") or f"afl-exec-{uuid.uuid4().hex[:8]}"

    resp = client.start_execution(
        stateMachineArn=sm_arn,
        name=exec_name,
        input=input_payload,
    )

    start_date = resp.get("startDate", datetime.now(UTC))
    start_str = start_date.isoformat() if isinstance(start_date, datetime) else str(start_date)

    return {
        "result": {
            "execution_arn": resp["executionArn"],
            "state_machine_arn": sm_arn,
            "status": "RUNNING",
            "start_date": start_str,
            "stop_date": "",
            "input_payload": input_payload,
            "output_payload": "",
        },
    }


def _describe_execution_handler(payload: dict) -> dict[str, Any]:
    """Describe a state machine execution."""
    step_log = payload.get("_step_log")
    client = _sfn_client()
    exec_arn = payload.get("execution_arn", "")
    if step_log:
        step_log(f"DescribeExecution: {exec_arn}")

    resp = client.describe_execution(executionArn=exec_arn)

    start_date = resp.get("startDate", datetime.now(UTC))
    start_str = start_date.isoformat() if isinstance(start_date, datetime) else str(start_date)
    stop_date = resp.get("stopDate")
    stop_str = stop_date.isoformat() if isinstance(stop_date, datetime) else str(stop_date or "")

    return {
        "result": {
            "execution_arn": resp.get("executionArn", exec_arn),
            "state_machine_arn": resp.get("stateMachineArn", ""),
            "status": resp.get("status", "SUCCEEDED"),
            "start_date": start_str,
            "stop_date": stop_str,
            "input_payload": resp.get("input", "{}"),
            "output_payload": resp.get("output", "{}"),
        },
    }


def _delete_state_machine_handler(payload: dict) -> dict[str, Any]:
    """Delete a state machine."""
    step_log = payload.get("_step_log")
    client = _sfn_client()
    sm_arn = payload.get("state_machine_arn", "")
    if step_log:
        step_log(f"DeleteStateMachine: {sm_arn}")

    client.delete_state_machine(stateMachineArn=sm_arn)

    return {
        "config": {
            "state_machine_arn": sm_arn,
            "state_machine_name": "",
            "definition": "",
            "role_arn": "",
            "creation_date": "",
            "status": "DELETING",
        },
    }


def _list_executions_handler(payload: dict) -> dict[str, Any]:
    """List executions of a state machine."""
    step_log = payload.get("_step_log")
    client = _sfn_client()
    sm_arn = payload.get("state_machine_arn", "")
    if step_log:
        step_log(f"ListExecutions: {sm_arn}")
    status_filter = payload.get("status_filter", "")
    max_results = payload.get("max_results", 100)

    kwargs: dict[str, Any] = {
        "stateMachineArn": sm_arn,
        "maxResults": int(max_results),
    }
    if status_filter:
        kwargs["statusFilter"] = status_filter

    resp = client.list_executions(**kwargs)

    executions = resp.get("executions", [])
    if executions:
        ex = executions[0]
        start_date = ex.get("startDate", datetime.now(UTC))
        start_str = start_date.isoformat() if isinstance(start_date, datetime) else str(start_date)
        return {
            "info": {
                "execution_arn": ex.get("executionArn", ""),
                "state_machine_arn": ex.get("stateMachineArn", sm_arn),
                "status": ex.get("status", ""),
                "start_date": start_str,
                "name": ex.get("name", ""),
            },
        }

    return {
        "info": {
            "execution_arn": "",
            "state_machine_arn": sm_arn,
            "status": "",
            "start_date": "",
            "name": "",
        },
    }


# RegistryRunner dispatch adapter
_DISPATCH = {
    f"{NAMESPACE}.CreateStateMachine": _create_state_machine_handler,
    f"{NAMESPACE}.StartExecution": _start_execution_handler,
    f"{NAMESPACE}.DescribeExecution": _describe_execution_handler,
    f"{NAMESPACE}.DeleteStateMachine": _delete_state_machine_handler,
    f"{NAMESPACE}.ListExecutions": _list_executions_handler,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_stepfunctions_handlers(poller) -> None:
    """Register all Step Functions event facet handlers with the poller."""
    for fqn, func in _DISPATCH.items():
        poller.register(fqn, func)
        log.debug("Registered stepfunctions handler: %s", fqn)
