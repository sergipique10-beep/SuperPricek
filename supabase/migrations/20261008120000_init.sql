-- Esquema inicial: provincias, zonas de precio e histórico por periodos.
-- Ver docs/superpowers/specs/2026-10-08-supabase-multi-provincia-design.md

create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;

create table supermarkets (
  id           text primary key,                 -- 'mercadona'
  display_name text not null
);

create table provinces (
  id          text primary key,                  -- 'barcelona'
  name        text not null,
  postal_code text not null check (postal_code ~ '^\d{5}$')
);

-- Dónde fija precios cada cadena (en Mercadona, el almacén)
create table price_zones (
  id          bigint generated always as identity primary key,
  supermarket text not null references supermarkets(id),
  code        text not null,                     -- 'bcn1', '3968'
  active      boolean not null default true,
  unique (supermarket, code),
  unique (id, supermarket)                       -- para la FK compuesta de abajo
);

-- Qué zona usa cada provincia en cada cadena (una por cadena)
create table province_zones (
  province_id text   not null references provinces(id) on delete cascade,
  supermarket text   not null,
  zone_id     bigint not null,
  primary key (province_id, supermarket),
  foreign key (zone_id, supermarket) references price_zones(id, supermarket)
);
create index province_zones_zone_id_idx on province_zones (zone_id);

create table products (
  id          bigint generated always as identity primary key,
  supermarket text not null references supermarkets(id),
  external_id text not null,
  name        text not null,
  brand       text,
  category    text,
  packaging   text,
  size        numeric(12,4),
  size_unit   text,
  ean         text,
  image_url   text,
  url         text,
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now(),
  unique (supermarket, external_id)
);
create index products_name_trgm_idx on products
  using gin (lower(name) extensions.gin_trgm_ops);

create table price_periods (
  id             bigint generated always as identity primary key,
  product_id     bigint not null references products(id),
  zone_id        bigint not null references price_zones(id),
  price          numeric(10,2) not null,
  unit_price     numeric(12,4),
  unit           text,
  previous_price numeric(10,2),
  on_promotion   boolean not null default false,
  valid_from     date not null,
  last_seen      date not null,
  check (last_seen >= valid_from),
  unique (product_id, zone_id, valid_from)       -- sirve también para buscar el último periodo
);
create index price_periods_zone_last_seen_idx on price_periods (zone_id, last_seen);

create table import_runs (
  id          bigint generated always as identity primary key,
  zone_id     bigint not null references price_zones(id),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  import_date date not null,
  products    integer not null default 0,
  status      text not null default 'running' check (status in ('running', 'ok', 'error')),
  error       text
);
create index import_runs_zone_id_idx on import_runs (zone_id, import_date);

-- Supabase expone `public` en su API REST: RLS activado y sin políticas,
-- así anon/authenticated no ven nada. Importador y API usan conexión directa.
alter table supermarkets   enable row level security;
alter table provinces      enable row level security;
alter table price_zones    enable row level security;
alter table province_zones enable row level security;
alter table products       enable row level security;
alter table price_periods  enable row level security;
alter table import_runs    enable row level security;
