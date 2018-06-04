CREATE TABLE seen (
   id INTEGER PRIMARY KEY,
   site INTEGER,
   pet TEXT,
   seen DATETIME);

CREATE UNIQUE INDEX site_pet ON seen (site, pet);
CREATE INDEX seen_seen ON seen (seen);
