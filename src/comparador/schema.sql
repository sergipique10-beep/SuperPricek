-- Esquema compatible con SQLite y, con cambios mínimos, con PostgreSQL.

CREATE TABLE IF NOT EXISTS supermarkets (
    id           TEXT PRIMARY KEY,          -- "mercadona"
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supermarket  TEXT NOT NULL REFERENCES supermarkets(id),
    external_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    brand        TEXT,
    category     TEXT,
    packaging    TEXT,
    size         REAL,
    size_unit    TEXT,
    ean          TEXT,
    image_url    TEXT,
    url          TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    UNIQUE (supermarket, external_id)
);

CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_products_ean  ON products(ean);

-- Histórico: una fila cada vez que vemos un precio.
CREATE TABLE IF NOT EXISTS price_observations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL REFERENCES products(id),
    price          REAL NOT NULL,
    unit_price     REAL,
    unit           TEXT,
    previous_price REAL,
    on_promotion   INTEGER NOT NULL DEFAULT 0,
    region         TEXT,
    observed_at    TEXT NOT NULL,
    observed_date  TEXT NOT NULL,           -- YYYY-MM-DD, para no duplicar en el mismo día
    UNIQUE (product_id, region, observed_date)
);

CREATE INDEX IF NOT EXISTS idx_obs_product ON price_observations(product_id, observed_at);

-- Registro de cada ejecución del importador.
CREATE TABLE IF NOT EXISTS import_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supermarket  TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    products     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'running',  -- running | ok | error
    error        TEXT
);
