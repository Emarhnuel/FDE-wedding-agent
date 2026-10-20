# Private, versioned bucket for uploaded knowledge files (KNOWLEDGE_BUCKET in src/storage.py).
# Never public and never the website: Amplify hosts the frontend separately.
resource "aws_s3_bucket" "knowledge" {
  # Bucket names are global across AWS, so the account ID keeps this one unique.
  bucket        = "${local.name_prefix}-knowledge-${local.account_id}"
  force_destroy = var.force_destroy_buckets
}

# Keeps every earlier copy of an original, for audit and recovery.
resource "aws_s3_bucket_versioning" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "knowledge" {
  bucket = aws_s3_bucket.knowledge.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
