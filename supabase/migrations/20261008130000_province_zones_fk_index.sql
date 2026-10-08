-- El índice debe cubrir la FK compuesta (zone_id, supermarket), no solo zone_id
-- (aviso "unindexed_foreign_keys" del revisor de Supabase).
drop index if exists province_zones_zone_id_idx;
create index province_zones_zone_supermarket_idx on province_zones (zone_id, supermarket);
