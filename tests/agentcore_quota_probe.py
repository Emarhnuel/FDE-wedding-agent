"""Check whether this AWS account can create any Bedrock AgentCore runtime at all.

Creates a throwaway runtime called `quota_probe`, unrelated to the Juniper Hollow agent, and deletes
it straight away if creation works. Not a pytest test (no test_ prefix); run it by hand:

    uv run python tests/agentcore_quota_probe.py

It needs an existing AgentCore execution role and any ARM64 image in ECR only because the API
requires them; the probe never runs a session.
"""

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ROLE_NAME = "juniper-hollow-dev-agentcore-runtime"  # any role that trusts bedrock-agentcore works
REPOSITORY = "juniper-hollow-dev-agentcore"  # any ARM64 image works; it is never started


def main() -> None:
    account = boto3.client("sts").get_caller_identity()["Account"]
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    quotas = boto3.client("service-quotas", region_name=REGION)

    print(f"Account {account}, region {REGION}")
    pages = quotas.get_paginator("list_service_quotas").paginate(ServiceCode="bedrock-agentcore")
    for quota in (q for page in pages for q in page["Quotas"]):
        if quota["QuotaName"] in {"Total Agents per Account", "Endpoints per Agent", "Versions per Agent"}:
            print(f"  applied quota  {quota['QuotaName']}: {quota['Value']:g}")

    image = boto3.client("ecr", region_name=REGION).describe_images(repositoryName=REPOSITORY)["imageDetails"][0]
    image_uri = f"{account}.dkr.ecr.{REGION}.amazonaws.com/{REPOSITORY}:{image['imageTags'][0]}"
    role_arn = f"arn:aws:iam::{account}:role/{ROLE_NAME}"

    print("Creating test runtime quota_probe ...")
    try:
        runtime = control.create_agent_runtime(
            agentRuntimeName="quota_probe",
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
        )
    except ClientError as error:
        print(f"FAILED: {error.response['Error']['Code']}: {error.response['Error']['Message']}")
        return

    print(f"CREATED {runtime['agentRuntimeArn']}: AgentCore works on this account. Deleting it ...")
    control.delete_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])
    print("Deleted.")


if __name__ == "__main__":
    main()
