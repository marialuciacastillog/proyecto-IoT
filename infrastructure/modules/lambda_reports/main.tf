# Fase 9: Lambda que procesa cada reporte nuevo que el Batch deja en S3.

# En Learner Lab no se pueden crear roles, así que se usa el que ya viene: LabRole
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

# Cola donde la Lambda publica sus eventos (REPORT_VALIDATED o REPORT_REJECTED)
resource "aws_sqs_queue" "report_events" {
  name                      = "report-events"
  message_retention_seconds = 86400
}

# Empaqueta el código de la Lambda en un .zip
data "archive_file" "code" {
  type        = "zip"
  source_file = var.source_file
  output_path = "${path.module}/lambda_reports.zip"
}

resource "aws_lambda_function" "report_processor" {
  function_name    = "report-processor"
  role             = data.aws_iam_role.lab_role.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      EVENTS_QUEUE_URL = aws_sqs_queue.report_events.url
    }
  }
}

# Deja que S3 llame a la Lambda
resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.report_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::${var.bucket_name}"
}

# El disparador: cada archivo .json nuevo en warehouse-platform/reports/ activa la Lambda.
# Solo mira reports/, y la Lambda escribe en exports/ y audit/, así que no se llama a sí misma.
resource "aws_s3_bucket_notification" "reports_created" {
  bucket = var.bucket_name

  lambda_function {
    lambda_function_arn = aws_lambda_function.report_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "warehouse-platform/reports/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.allow_s3]
}
