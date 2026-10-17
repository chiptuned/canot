CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  name TEXT NOT NULL, company TEXT NOT NULL, email TEXT NOT NULL,
  platform TEXT NOT NULL, renewal TEXT NOT NULL, message TEXT,
  source TEXT DEFAULT 'contact', partage TEXT
);

-- Migration (une fois sur la base existante) :
--   ALTER TABLE leads ADD COLUMN source TEXT DEFAULT 'contact';
--   ALTER TABLE leads ADD COLUMN partage TEXT;
