CREATE TABLE IF NOT EXISTS shipments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER NOT NULL,
    carrier        TEXT    NOT NULL,
    label_id       TEXT    NOT NULL UNIQUE,
    weight_g       INTEGER NOT NULL,
    dest_country   TEXT    NOT NULL,
    charge_cents   INTEGER NOT NULL,
    declared_cents INTEGER,
    status         TEXT    NOT NULL DEFAULT 'labelled',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS shipments_customer ON shipments (customer_id, id);

CREATE TABLE IF NOT EXISTS shipment_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL REFERENCES shipments (id),
    kind        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
