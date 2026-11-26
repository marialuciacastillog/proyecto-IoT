# Seguridad del sistema

## 1. Comunicaciones (RNF-03)

Desde internet solo se entra por **HTTPS (puerto 443)**. Cada máquina tiene su certificado, firmado por la
autoridad certificadora (CA) del proyecto.

| Quién le habla a quién | Cómo va |
|---|---|
| Navegador → front de la central o de una bodega | HTTPS en el 443. El HTTP (8080, 8081-8083) solo redirige a HTTPS |
| Scripts → APIs de la central | HTTPS por el front de la central: `https://central/svc/identity`, `/svc/catalog`, `/svc/reports`... |
| Scripts → API de una bodega | HTTPS por el front de la bodega: `https://wh-001/api/...` (detrás está el balanceador) |
| Front de una bodega → central (login y catálogo) | HTTPS, y Nginx **revisa el certificado** de la central con la CA (`proxy_ssl_verify`) |
| Gateway IoT de una bodega → ingesta de la central | HTTPS, y Python **revisa el certificado** con la CA (`SSL_CERT_FILE`) |
| Bodegas ↔ central (movimientos, catálogo, transferencias, alertas) | SQS y SNS, que siempre van por HTTPS |
| APIs → S3 (reportes y contratos) | HTTPS. Los contratos además quedan cifrados en S3 (SSE-S3) |
| Contenedores dentro de una misma máquina | Red privada de Docker: ese tráfico no sale de la máquina |

Los puertos de las APIs (8000-8010) **ya no están abiertos a internet**. El security group solo los deja usar
a las IPs fijas de las máquinas del proyecto (por ejemplo, la prueba de carga corre desde la central).

Los puertos de PostgreSQL y Redis nunca se abrieron en el security group.

### Por qué una CA propia

En el Learner Lab no tenemos un dominio propio. Sin dominio no se puede sacar un certificado público
(ni con ACM ni con Let's Encrypt). Por eso Terraform (`infrastructure/https.tf`) crea:

- una **CA del proyecto** con una llave ECDSA P-256 que dura 2 años,
- un **certificado por máquina** que dura 1 año. Lleva la IP fija de la máquina y un nombre interno,
  por ejemplo `central.proyecto-iot.internal`.

Cada máquina tiene una **IP elástica fija**. Las IPs están afuera del módulo de las EC2, así que no cambian
al recrear las máquinas. Por eso los certificados siguen siendo válidos.

El navegador avisa la primera vez porque no conoce la CA. Para quitar el aviso en Windows:

1. Guardar la CA con `terraform -chdir=infrastructure output -raw ca_certificate > ca-proyecto.crt`.
2. Hacer doble clic en el archivo, luego **Instalar certificado**, **Usuario actual** y
   **Entidades de certificación raíz de confianza**.

Solo se aceptan TLS 1.2 y 1.3. Además, el front central manda HSTS para que el navegador use siempre HTTPS.

## 2. Credenciales (RNF-02)

- **Contraseñas:** se guardan con bcrypt, nunca en texto plano. La cuenta se bloquea 5 minutos después de 5 intentos fallidos.
- **Tokens:** son JWT RS256. Solo la central tiene la llave privada; las bodegas solo pueden revisar los tokens, no fabricarlos.
- **Clave inicial del admin:** se crea al azar en la máquina. Solo se lee con `sudo`.
- **Llaves en las máquinas:** las de los tokens y las de HTTPS están en `/opt/proyecto/secrets` y la llave privada tiene permisos 600.
- **Descargas de S3:** los links de reportes y contratos duran 15 minutos.

Las llaves privadas también quedan en el estado de Terraform (`terraform.tfstate`). Ese archivo está en el
computador de quien despliega y **no se sube al repositorio**.

## 3. Permisos de AWS (IAM) con mínimo privilegio

**Limitación del Learner Lab:** no deja crear roles ni políticas (`iam:CreateRole` está bloqueado). Todas las
máquinas usan `LabInstanceProfile` y la Lambda usa `LabRole`, que tienen muchos más permisos de los necesarios.

Por eso los permisos quedan **diseñados**, no aplicados. Las políticas están en `infrastructure/iam/`. En una
cuenta normal de AWS se crea un rol por tipo de componente con su política, y cada máquina usa el suyo:

| Rol | Lo usa | Puede hacer | No puede hacer |
|---|---|---|---|
| `rol-central` (`rol-central.json`) | EC2 de la central | Leer sus colas: sync, audit, transfer-events, reports e iot-*. Mandar órdenes a `transfer-commands-wh-*`. Atender las DLQ. Publicar en `catalog-events` e `iot-events`. Escribir y leer solo en `reports/`, `contratos/` y `warehouse-platform/reports/` del bucket | Borrar colas o buckets, tocar otras carpetas del bucket, crear máquinas |
| `rol-bodega-wh-00X` (`rol-bodega-wh-001.json`) | EC2 de cada bodega (cambiando el número) | Publicar en `inventory-events`. Leer **solo sus** colas `catalog-wh-00X` y `transfer-commands-wh-00X`. Responder en `transfer-events`. El gateway IoT puede buscar la IP de la central (`ec2:DescribeInstances`) | Leer las colas de otra bodega, leer S3, publicar en el catálogo |
| `rol-lambda-reportes` (`rol-lambda-reportes.json`) | Lambda `report-processor` | Leer `warehouse-platform/reports/`, escribir en `exports/` y `audit/lambda/`, avisar en `report-events` y escribir sus logs | Leer contratos, borrar objetos, tocar colas que no son la suya |

Para usarlos se crea el rol con su documento de confianza (`confianza-ec2.json` para las EC2, o
`confianza-lambda.json` para la Lambda), se le pone la política, y en las EC2 se arma un instance profile.
En `modules/ec2` se cambiaría `iam_instance_profile = "LabInstanceProfile"` por el perfil de cada máquina.

Las políticas se revisaron con **parliament**, una herramienta que revisa políticas de IAM, y no tienen errores.
`ec2:DescribeInstances` queda con `"Resource": "*"` porque AWS no deja limitar esa acción a una sola máquina.

Las cuentas de los usuarios del sistema (Admin Global, Admin por Bodega, Encargado, Líder) **no** son usuarios
de IAM. Sus permisos los revisa cada servicio con el token (RNF-01), y nadie entra a AWS con ellas.
