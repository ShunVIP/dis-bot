import sqlite3

conn = sqlite3.connect("datebase/wwm.db")
cur = conn.cursor()

cur.execute("PRAGMA table_info(raw_records)")
for row in cur.fetchall():
    print(row)

conn.close()
