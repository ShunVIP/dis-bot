import sqlite3

conn = sqlite3.connect("datebase/wwm.db")
cur = conn.cursor()

# RAW НЕ ТРОГАЕМ
cur.executescript("""
DELETE FROM aliases;
DELETE FROM entity_sources;
DELETE FROM entities;
DELETE FROM refresh_runs;
""")

conn.commit()
conn.close()
print("Normalized layer cleared (entities/entity_sources/aliases/refresh_runs). RAW untouched.")
