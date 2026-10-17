"""Reset demo script for learner 'rohan'.

Phase 10, Step 1:
- Deletes ONLY the learner 'rohan' and associated records
- Strictly never touches other learners (judges' test learners)
- Recreates learner 'rohan' with fresh realistic demo history via seed_rohan()
- Prints full audit report of actions performed
"""

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database import get_db_connection, init_db
from scripts.seed_demo import get_api_client, seed_rohan


def reset_rohan(client: object) -> None:
    """Safely reset and recreate learner 'rohan' while protecting all other learners."""
    init_db()
    conn = get_db_connection()

    print("=" * 80)
    print(" RESETTING DEMO LEARNER 'rohan'")
    print("=" * 80)

    cur = conn.cursor()

    # Audit check: count other learners
    cur.execute("SELECT id FROM learners WHERE id != 'rohan'")
    other_learners_before = [r[0] for r in cur.fetchall()]
    print(f"Other learners present in database: {len(other_learners_before)} {other_learners_before}")

    # Check if rohan exists
    cur.execute("SELECT id FROM learners WHERE id = 'rohan'")
    rohan_exists = cur.fetchone() is not None

    if rohan_exists:
        print("Learner 'rohan' found in database. Purging existing records...")
        with conn:
            conn.execute("DELETE FROM submissions WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM mastery WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM drafts WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM achievements WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM xp_events WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM predictions WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM mistakes WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM counterexamples WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM explanations WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM sessions WHERE learner_id = 'rohan';")
            conn.execute("DELETE FROM learners WHERE id = 'rohan';")
        print("  [OK] Learner 'rohan' deleted from database.")
    else:
        print("Learner 'rohan' does not currently exist. Proceeding to fresh creation...")

    # Audit check: verify other learners untouched
    cur.execute("SELECT id FROM learners WHERE id != 'rohan'")
    other_learners_after = [r[0] for r in cur.fetchall()]
    assert other_learners_before == other_learners_after, "SAFETY VIOLATION: Non-rohan learners were modified!"
    print(f"  [SAFETY VERIFIED] Zero other learners touched (count: {len(other_learners_after)}).")

    conn.close()

    # Recreate rohan by running real submissions through the API
    print("\nRecreating learner 'rohan' via API submissions...")
    seed_rohan(client)
    print("\n[COMPLETE] Learner 'rohan' has been cleanly reset and re-seeded.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset and re-seed demo data for learner 'rohan'")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of API server")
    args = parser.parse_args()

    client, desc = get_api_client(args.url)
    print(f"Client: {desc}")
    reset_rohan(client)


if __name__ == "__main__":
    main()
