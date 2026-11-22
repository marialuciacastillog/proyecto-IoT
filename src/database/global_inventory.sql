-- Tabla del warehouse central. NO SE CAMBIA SIN HABLARLO.
CREATE TABLE global_inventory (
    warehouse_id VARCHAR(10),
    product_id   VARCHAR(20),
    quantity     INT,
    updated_at   TIMESTAMP,
    PRIMARY KEY (warehouse_id, product_id)
);
