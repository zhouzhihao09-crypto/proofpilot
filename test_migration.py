import tempfile
import os
import sqlite3

# Create a database with OLD schema (no new columns)
db_path = os.path.join(tempfile.mkdtemp(), 'old_test.db')
conn = sqlite3.connect(db_path)

# Old schema for claims (without new columns)
conn.executescript("""
CREATE TABLE IF NOT EXISTS claims (
    claim_id                TEXT PRIMARY KEY,
    task_id                 TEXT NOT NULL,
    text                    TEXT NOT NULL,
    verification_status      TEXT NOT NULL,
    supporting_evidence_ids TEXT NOT NULL DEFAULT '[]',
    conflicting_evidence_ids TEXT NOT NULL DEFAULT '[]',
    explanation              TEXT NOT NULL DEFAULT '',
    created_at              TEXT,
    updated_at              TEXT
);
""")

# Create schema_meta with version 1
conn.execute("""
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)
""")
conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)", ("version", "1"))
conn.commit()

# Check schema
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(claims)')
for row in cursor.fetchall():
    print(row)

print('--- Now run migration ---')

# Now simulate the migration
new_columns = {
    'verification_method': 'TEXT',
    'deterministic_status': 'TEXT',
    'semantic_status': 'TEXT',
    'semantic_confidence': 'REAL',
    'semantic_explanation': 'TEXT',
    'deterministic_confidence': 'REAL',
}
for column, col_type in new_columns.items():
    try:
        conn.execute(f'ALTER TABLE verification_results ADD COLUMN {column} {col_type}')
        print(f'Added {column} to verification_results')
    except sqlite3.OperationalError as e:
        print(f'Failed to add {column} to verification_results: {e}')

claim_columns = {
    'verification_method': 'TEXT',
    'deterministic_status': 'TEXT',
    'semantic_status': 'TEXT',
    'semantic_confidence': 'REAL',
    'semantic_explanation': 'TEXT',
}
for column, col_type in claim_columns.items():
    try:
        conn.execute(f'ALTER TABLE claims ADD COLUMN {column} {col_type}')
        print(f'Added {column} to claims')
    except sqlite3.OperationalError as e:
        print(f'Failed to add {column} to claims: {e}')

conn.commit()

# Check schema again
cursor.execute('PRAGMA table_info(claims)')
for row in cursor.fetchall():
    print(row)