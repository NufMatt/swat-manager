import sqlite3
from datetime import datetime

DATABASE_FILE = "data.db"

def migrate_datetimes():
    """
    Migrate all datetime strings in the entries table so that they use a space
    separator between the date and time. This ensures consistency across the DB.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    # Fetch all thread_id, starttime, and endtime values
    cursor.execute("SELECT thread_id, starttime, endtime FROM entries")
    rows = cursor.fetchall()

    for thread_id, starttime_str, endtime_str in rows:
        try:
            # Parse the stored ISO datetime (even if it contains 'T')
            dt_start = datetime.fromisoformat(starttime_str)
            # Reformat using a space between date and time
            new_start = dt_start.isoformat(" ")
        except Exception as e:
            print(f"❌ Error converting starttime for thread {thread_id}: {e}")
            continue  # Skip updating this row if conversion fails

        new_end = None
        if endtime_str:
            try:
                dt_end = datetime.fromisoformat(endtime_str)
                new_end = dt_end.isoformat(" ")
            except Exception as e:
                print(f"❌ Error converting endtime for thread {thread_id}: {e}")
                # Decide here if you want to leave it unchanged or set to NULL

        # Update the row with the new formatted datetime strings
        cursor.execute(
            "UPDATE entries SET starttime = ?, endtime = ? WHERE thread_id = ?",
            (new_start, new_end, thread_id)
        )
        print(f"✅ Updated thread {thread_id}")

    conn.commit()
    conn.close()

def update_endtime_for_thread(thread_id, new_endtime_str):
    """
    Update the endtime for a specific thread using the provided datetime string.
    The new_endtime_str should be in a format that datetime.fromisoformat can parse,
    e.g., "2025-02-10 15:30:00.000000".
    """
    try:
        dt_end = datetime.fromisoformat(new_endtime_str)
        new_end_formatted = dt_end.isoformat(" ")
    except Exception as e:
        print(f"❌ Error parsing the new endtime: {e}")
        return

    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE entries SET endtime = ? WHERE thread_id = ?", (new_end_formatted, thread_id))
    conn.commit()
    print(f"✅ Updated thread {thread_id} with new endtime: {new_end_formatted}")
    conn.close()

if __name__ == "__main__":
    print("Starting migration of datetime values...")
    migrate_datetimes()
    print("Migration complete.")

    thread_id = input("Enter thread_id to update endtime (or leave empty to skip): ").strip()
    if thread_id:
        new_endtime_str = input("Enter new endtime (YYYY-MM-DD HH:MM:SS.microseconds): ").strip()
        update_endtime_for_thread(thread_id, new_endtime_str)
    else:
        print("No thread_id provided. Exiting.")
