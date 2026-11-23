# El Learner Lab bloquea que Terraform lea algunas configuraciones de los buckets
# (s3:GetBucketObjectLockConfiguration), así que el bucket se crea una sola vez con:
#   aws s3 mb s3://proyecto-iot-reportes-mlcg-2
# y este módulo solo entrega su nombre a los demás.
# Los buckets nuevos de S3 ya vienen privados (sin acceso público) por defecto.
