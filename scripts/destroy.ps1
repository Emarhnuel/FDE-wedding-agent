# Destroy one environment's stack (never the bootstrap state bucket or the GitHub deploy role).
#
#   .\scripts\destroy.ps1 -Environment dev        (or prod)
#
# Asks you to type the environment name first. Same steps as scripts/destroy.sh.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("dev", "prod")]
    [string]$Environment
)

$ErrorActionPreference = "Stop"

function Assert-Success([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit code $LASTEXITCODE)." }
}

# True when a native command succeeds. Used for checks that are expected to fail (e.g. "does this image
# exist?"): Windows PowerShell 5.1 turns their error output into a terminating error under "Stop".
function Test-Command([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) }
    finally { $ErrorActionPreference = $previous }
}

$ProjectName = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "juniper-hollow" } # must match terraform.tfvars
$AwsRegion = if ($env:DEFAULT_AWS_REGION) { $env:DEFAULT_AWS_REGION } else { "us-east-1" }
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

foreach ($tool in "aws", "terraform") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing required tool: $tool" }
}

$AwsAccountId = aws sts get-caller-identity --query Account --output text
Assert-Success "Reading the AWS account (run 'aws configure' first)"
$StateBucket = "$ProjectName-terraform-state-$AwsAccountId"

Write-Host "This permanently deletes $ProjectName-$Environment in account $AwsAccountId ($AwsRegion):"
Write-Host "the website, API, functions, queues, uploaded knowledge files, and images."
if ($env:CI -ne "true") {
    $confirmation = Read-Host "Type the environment name ($Environment) to confirm"
    if ($confirmation -ne $Environment) { throw "Cancelled." }
}

# Destroy doesn't use the image, but the variable is required.
$TfVars = @("-var", "image_tag=destroy", "-var", "force_destroy_buckets=true")
if ($Environment -eq "prod") { $TfVars = @("-var-file=prod.tfvars") + $TfVars }

Push-Location (Join-Path $RootDir "terraform")
try {
    terraform init -input=false "-backend-config=bucket=$StateBucket" "-backend-config=region=$AwsRegion"
    Assert-Success "terraform init"

    if (-not (Test-Command { terraform workspace select $Environment })) {
        Write-Host "Workspace $Environment does not exist; nothing to destroy."
        return
    }

    # Turn on force_destroy first, so the bucket (with every file version) and the ECR repository
    # (with its images) can be deleted even when they are not empty. prod keeps it off in normal deploys.
    Write-Host "==> Allowing the bucket and repository to be deleted with their contents"
    terraform apply -input=false -auto-approve @TfVars "-target=aws_s3_bucket.knowledge" "-target=aws_ecr_repository.app"
    Assert-Success "terraform apply (force destroy)"

    Write-Host "==> Terraform destroy"
    terraform destroy -input=false -auto-approve @TfVars
    Assert-Success "terraform destroy"

    Write-Host ""
    Write-Host "Destroyed $ProjectName-$Environment."
    if ($Environment -eq "prod") {
        Write-Host "Note: the prod secret stays recoverable for 30 days, so its name can't be reused until then."
    }
}
finally {
    Pop-Location
}
