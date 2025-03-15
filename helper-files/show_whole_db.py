import sqlite3
import pprint

DATABASE_FILE = "data.db"

def show_all_entries():
    # Open the database in read-only mode by using a URI
    conn = sqlite3.connect(f'file:{DATABASE_FILE}?mode=ro', uri=True)
    cursor = conn.cursor()

    # Query all rows from the entries table
    cursor.execute("SELECT * FROM entries")
    rows = cursor.fetchall()

    # Get column names from the cursor description
    column_names = [description[0] for description in cursor.description]

    # Format each row as a dictionary for readability
    for row in rows:
        entry = dict(zip(column_names, row))
        pprint.pprint(entry)
        print("-" * 50)

    conn.close()

if __name__ == "__main__":
    show_all_entries()
