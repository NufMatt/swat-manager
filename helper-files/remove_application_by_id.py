import sqlite3

def remove_application_by_thread_id(thread_id: str) -> bool:
    try:
        conn = sqlite3.connect("data.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM application_threads WHERE thread_id = ?", (thread_id,))
        conn.commit()
        return cursor.rowcount > 0  # Returns True if at least one record was deleted.
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False
    finally:
        conn.close()

# Example usage:
if __name__ == "__main__":
    thread_id_to_remove = "1357138858067624116"  # Replace with the actual thread ID.
    if remove_application_by_thread_id(thread_id_to_remove):
        print("Application removed successfully.")
    else:
        print("No application found with that thread ID or an error occurred.")
