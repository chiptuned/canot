CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  name TEXT NOT NULL, company TEXT NOT NULL, email TEXT NOT NULL,
  platform TEXT NOT NULL, renewal TEXT NOT NULL, message TEXT,
  source TEXT DEFAULT 'contact', partage TEXT
);
-- source: 'contact' (home page form) or 'offre' (qualified request from the offers page).
-- partage: the scan-share.json attached to a qualified request (schema canot.share/1, or the former canot.partage/1).
--          The column keeps its original name so the existing database needs no change.

-- Migration (once, on a database created before these columns existed):
--   ALTER TABLE leads ADD COLUMN source TEXT DEFAULT 'contact';
--   ALTER TABLE leads ADD COLUMN partage TEXT;
