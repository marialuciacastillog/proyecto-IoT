# Fase 11: logs estructurados. Cada línea es un JSON con los mismos campos en todos los servicios,
# así se pueden filtrar y contar (por ejemplo, todas las líneas de un correlation_id).
import json
from datetime import datetime, timezone


def logJson(service, level, event, fields):
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "level": level,
        "event": event
    }
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)
