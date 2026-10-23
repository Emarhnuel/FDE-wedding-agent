# Deploy one environment end to end: images -> Terraform -> website.
#
#   .\scripts\deploy.ps1 -Environment dev        (or prod)
#
# Needs: aws CLI (logged in), terraform >= 1.10, Docker Desktop (buildx), git.
# Works in Windows PowerShell 5.1 and PowerShell 7. Same steps as scripts/deploy.sh.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("dev", "prod")]
    [string]$Environment
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue" # Invoke-WebRequest is very slow with a progress bar in PS 5.1

# Native tools report failure through their exit code, not a PowerShell error.
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
$NamePrefix = "$ProjectName-$Environment"

foreach ($tool in "aws", "terraform", "docker", "git") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing required tool: $tool" }
}

$AwsAccountId = aws sts get-caller-identity --query Account --output text
Assert-Success "Reading the AWS account (run 'aws configure' first)"
$Registry = "$AwsAccountId.dkr.ecr.$AwsRegion.amazonaws.com"
$StateBucket = "$ProjectName-terraform-state-$AwsAccountId" # created by terraform/bootstrap

# Image tag: the commit SHA. ECR tags are immutable, so uncommitted local changes get a unique tag
# instead of silently reusing the image already pushed for this commit.
if ($env:GITHUB_SHA) {
    $ImageTag = $env:GITHUB_SHA
} else {
    $ImageTag = git -C $RootDir rev-parse HEAD
    Assert-Success "Reading the git commit"
    if (git -C $RootDir status --porcelain) { $ImageTag = "$ImageTag-local-$(Get-Date -Format yyyyMMddHHmmss)" }
}

Write-Host "==> Deploying $NamePrefix to account $AwsAccountId ($AwsRegion), image tag $ImageTag"

$TfVars = @("-var", "image_tag=$ImageTag")
if ($Environment -eq "prod") { $TfVars += "-var-file=prod.tfvars" }

Push-Location (Join-Path $RootDir "terraform")
$SiteDir = Join-Path ([System.IO.Path]::GetTempPath()) "jh-site-$([guid]::NewGuid())"
try {
    Write-Host "==> Terraform init (state bucket $StateBucket)"
    terraform init -input=false "-backend-config=bucket=$StateBucket" "-backend-config=region=$AwsRegion"
    Assert-Success "terraform init"
    terraform workspace select -or-create $Environment
    Assert-Success "terraform workspace select"

    # 1. The image repositories must exist before the images can be pushed.
    Write-Host "==> Creating the ECR repositories"
    terraform apply -input=false -auto-approve @TfVars "-target=aws_ecr_repository.app" "-target=aws_ecr_lifecycle_policy.app"
    Assert-Success "terraform apply (ECR repositories)"

    # 2. Build and push both images (skipped if this tag is already in ECR).
    Write-Host "==> Logging in to ECR"
    aws ecr get-login-password --region $AwsRegion | docker login --username AWS --password-stdin $Registry
    Assert-Success "docker login"

    $images = @(
        @{ Repository = "$NamePrefix-lambda"; Platform = "linux/amd64"; Dockerfile = "Dockerfile" },
        @{ Repository = "$NamePrefix-agentcore"; Platform = "linux/arm64"; Dockerfile = "Dockerfile.agentcore" }
    )
    foreach ($image in $images) {
        if (Test-Command { aws ecr describe-images --region $AwsRegion --repository-name $image.Repository --image-ids "imageTag=$ImageTag" }) {
            Write-Host "==> $($image.Repository):$ImageTag already in ECR; skipping build"
            continue
        }
        Write-Host "==> Building and pushing $($image.Repository):$ImageTag ($($image.Platform))"
        # --provenance=false: Lambda and AgentCore need a plain single-platform image manifest.
        docker buildx build --platform $image.Platform --provenance=false `
            -f (Join-Path $RootDir $image.Dockerfile) -t "$Registry/$($image.Repository):$ImageTag" --push $RootDir
        Assert-Success "docker build of $($image.Repository)"
    }

    # 3. Everything else: queue, secret, functions, AgentCore runtime, API, schedules, website app.
    Write-Host "==> Terraform apply"
    terraform apply -input=false -auto-approve @TfVars
    Assert-Success "terraform apply"

    $ApiUrl = terraform output -raw api_url
    $WebsiteUrl = terraform output -raw website_url
    $AmplifyAppId = terraform output -raw amplify_app_id
    $AmplifyBranch = terraform output -raw amplify_branch_name
    $AppSecretName = terraform output -raw app_secret_name
    Assert-Success "terraform output"

    # 4. Website: copy frontend/, point config.js at this environment's API, zip, and publish to Amplify.
    Write-Host "==> Publishing the website to Amplify"
    $SiteContent = Join-Path $SiteDir "site"
    New-Item -ItemType Directory -Path $SiteContent | Out-Null
    Copy-Item -Path (Join-Path $RootDir "frontend\*") -Destination $SiteContent -Recurse -Force
    [System.IO.File]::WriteAllText((Join-Path $SiteContent "config.js"),
        "// Written by scripts/deploy.ps1 for $Environment.`nwindow.API_BASE_URL = `"$ApiUrl`";`n")
    $ZipPath = Join-Path $SiteDir "site.zip"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($SiteContent, $ZipPath)

    $deployment = aws amplify create-deployment --region $AwsRegion --app-id $AmplifyAppId --branch-name $AmplifyBranch --output json | ConvertFrom-Json
    Assert-Success "amplify create-deployment"
    Invoke-WebRequest -Uri $deployment.zipUploadUrl -Method Put -InFile $ZipPath -UseBasicParsing | Out-Null
    aws amplify start-deployment --region $AwsRegion --app-id $AmplifyAppId --branch-name $AmplifyBranch --job-id $deployment.jobId | Out-Null
    Assert-Success "amplify start-deployment"

    $status = ""
    for ($i = 0; $i -lt 60; $i++) { # up to 5 minutes
        $status = aws amplify get-job --region $AwsRegion --app-id $AmplifyAppId --branch-name $AmplifyBranch `
            --job-id $deployment.jobId --query "job.summary.status" --output text
        Assert-Success "amplify get-job"
        if ($status -in @("SUCCEED", "FAILED", "CANCELLED")) { break }
        Start-Sleep -Seconds 5
    }
    if ($status -ne "SUCCEED") { throw "Amplify deployment $($deployment.jobId) ended with status $status." }

    # 5. The app cannot start until its keys are in Secrets Manager (filled by hand, never by Terraform).
    if (-not (Test-Command { aws secretsmanager get-secret-value --region $AwsRegion --secret-id $AppSecretName --query VersionId --output text })) {
        Write-Warning "Secret $AppSecretName has no value yet. Add the app's keys as JSON (see terraform/secrets.tf) in the Secrets Manager console; the API and agent fail until then."
    }

    Write-Host ""
    Write-Host "Deployed $NamePrefix"
    Write-Host "  Website: $WebsiteUrl"
    Write-Host "  API:     $ApiUrl"
}
finally {
    Pop-Location
    if (Test-Path $SiteDir) { Remove-Item -Recurse -Force $SiteDir }
}
