CREATE TABLE seen (
   id INTEGER PRIMARY KEY,
   site INTEGER,
   pet TEXT);

CREATE UNIQUE INDEX site_pet ON seen (site, pet);
