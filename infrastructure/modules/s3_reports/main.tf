resource "aws_s3_bucket" "reports" {
  bucket = var.bucket_name

  tags = {
    Name    = "proyect-iot-reports"
    Service = "reports"
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}
