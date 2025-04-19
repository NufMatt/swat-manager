#!/usr/bin/env python3
import sqlite3
import os

DB_FILE = "data.db"

def get_tables(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    return [row[0] for row in cursor.fetchall()]

def get_table_info(conn, table_name):
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name});")
    return cursor.fetchall()

def get_all_contents(conn, table_name):
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {table_name};")
    return cursor.fetchall()

def main():
    if not os.path.exists(DB_FILE):
        print(f"Database file '{DB_FILE}' not found.")
        return

    conn = sqlite3.connect(DB_FILE)
    print(f"Connected to '{DB_FILE}'.\n")

    tables = get_tables(conn)
    if not tables:
        print("No tables found in the database.")
        return

    for table in tables:
        print(f"Table: {table}")
        print("-" * (len(table) + 8))
        # Get table structure
        info = get_table_info(conn, table)
        print("Columns:")
        for col in info:
            # PRAGMA table_info returns:
            # (cid, name, type, notnull, dflt_value, pk)
            cid, name, col_type, notnull, default, pk = col
            print(f"  {cid}: {name} ({col_type}) NotNull: {bool(notnull)} | Default: {default} | PrimaryKey: {bool(pk)}")
        print("\nData:")
        rows = get_all_contents(conn, table)
        if rows:
            for row in rows:
                print(row)
        else:
            print("  (No data)")
        print("\n" + "="*50 + "\n")
    conn.close()

if __name__ == "__main__":
    main()
