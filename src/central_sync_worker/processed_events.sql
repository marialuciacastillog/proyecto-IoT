-- Eventos que la central ya procesó. Sirve para ignorar mensajes repetidos.
CREATE TABLE IF NOT EXISTS processed_events (
    event_id     VARCHAR(64) PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT NOW()
);
