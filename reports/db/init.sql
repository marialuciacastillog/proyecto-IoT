CREATE TABLE IF NOT EXISTS global_inventory (
    warehouse_id VARCHAR(10),
    product_id   VARCHAR(20),
    quantity     INT NOT NULL,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (warehouse_id, product_id)
);

INSERT INTO global_inventory
    (warehouse_id, product_id, quantity)
VALUES
    ('wh-001', 'P-001', 90),
    ('wh-001', 'P-002', 35),
    ('wh-002', 'P-001', 120),
    ('wh-002', 'P-003', 70),
    ('wh-003', 'P-001', 45),
    ('wh-003', 'P-002', 60)
ON CONFLICT (warehouse_id, product_id) DO NOTHING;
