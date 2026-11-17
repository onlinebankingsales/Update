import os
import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

import data_store


class EmployeeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "records.db"
        self.environment = patch.dict(
            os.environ,
            {
                "INITIAL_ADMIN_USERNAME": data_store.SUPER_ADMIN_USERNAME,
                "INITIAL_ADMIN_PASSWORD": "safe-test-password",
                "DATA_DB_PATH": str(self.db_path),
            },
        )
        self.environment.start()
        self.database_path_patch = patch.object(data_store, "DATABASE_PATH", self.db_path)
        self.database_path_patch.start()
        self.backup_patch = patch.object(data_store, "_backup_sqlite_database")
        self.backup_patch.start()
        self.assertTrue(data_store.initialize_database())
        self.super_admin_id = data_store.authenticate(
            data_store.SUPER_ADMIN_USERNAME, "safe-test-password"
        )["id"]
        self.admin_id = data_store.create_account(
            self.super_admin_id, "admin01", "Cluster Admin", "safe-password-1", "admin"
        )
        self.rm_id = data_store.create_account(
            self.admin_id,
            "RM100",
            "Regional Manager",
            "safe-password-2",
            phone_number="+919000000001",
        )
        self.second_rm_id = data_store.create_account(
            self.admin_id,
            "RM200",
            "Second Manager",
            "safe-password-3",
            phone_number="+919000000002",
        )
        self.so_id = data_store.create_account(
            self.admin_id,
            "600123",
            "Sales Officer",
            "safe-password-4",
            phone_number="+919000000003",
        )

    def tearDown(self):
        self.backup_patch.stop()
        self.database_path_patch.stop()
        self.environment.stop()
        self.temp_dir.cleanup()

    def _add_record(self, employee_id, customer_name):
        data_store.add_record(
            employee_id,
            "2026-11-17",
            "09:00:00",
            customer_name,
            "9876543210",
            1.0,
            "Village",
            "Mandal",
            "District",
            "Interested",
            500000.0,
        )
        return data_store.get_user_records(employee_id)[0]["Proposal ID"]

    def test_employee_tag_and_manager_assignment_scope_proposals(self):
        users = {user["id"]: user for user in data_store.list_users(self.admin_id)}
        self.assertEqual(users[self.rm_id]["employee_tag"], "RM")
        self.assertEqual(users[self.so_id]["employee_tag"], "SO")

        proposal_id = self._add_record(self.so_id, "Assigned customer")
        data_store.assign_so_to_rm(self.admin_id, self.so_id, self.rm_id)
        with self.assertRaisesRegex(ValueError, "Reassign this RM"):
            data_store.update_employee_profile(
                self.admin_id, self.rm_id, employee_tag="SO"
            )

        rm_records = data_store.get_user_records(self.rm_id)
        other_rm_records = data_store.get_user_records(self.second_rm_id)
        self.assertEqual([row["Proposal ID"] for row in rm_records], [proposal_id])
        self.assertEqual(other_rm_records, [])
        with self.assertRaisesRegex(ValueError, "only update your own"):
            data_store.update_proposal(self.rm_id, proposal_id, stage="Login")
        with self.assertRaisesRegex(ValueError, "only use your own"):
            data_store.save_commitment_response(
                self.rm_id, "Login", "Yes", [proposal_id]
            )

        rm_proposal_id = self._add_record(self.rm_id, "RM-owned customer")
        data_store.update_proposal(self.rm_id, rm_proposal_id, stage="Login")
        rm_records = data_store.get_user_records(self.rm_id)
        rm_owned_record = next(
            row for row in rm_records if row["Proposal ID"] == rm_proposal_id
        )
        self.assertEqual(rm_owned_record["Stage"], "Login")
        with self.assertRaisesRegex(ValueError, "only update your own"):
            data_store.update_proposal(self.second_rm_id, proposal_id, stage="Login")

    def test_proposal_stage_advances_one_step_and_commitment_is_saved(self):
        proposal_id = self._add_record(self.so_id, "Commitment customer")
        with self.assertRaisesRegex(ValueError, "next stage"):
            data_store.update_proposal(
                self.so_id, proposal_id, stage="Pending for Sanction"
            )

        data_store.save_commitment_response(self.so_id, "Login", "Yes", [proposal_id])
        data_store.update_proposal(
            self.so_id, proposal_id, stage="Login", loan_amount=750000.0
        )
        record = data_store.get_user_records(self.so_id)[0]
        self.assertEqual(record["Stage"], "Login")
        self.assertEqual(record["Loan Amount"], 750000.0)

        commitment = data_store.get_daily_commitments(self.admin_id)
        self.assertEqual(len(commitment), 1)
        self.assertEqual(commitment[0]["Answer"], "Yes")
        self.assertEqual(commitment[0]["Customer"], "Commitment customer")

    def test_existing_database_is_migrated_with_tags_stage_and_phone_fields(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    employee_name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by_user_id INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    admin_owner_id INTEGER,
                    employee_name TEXT NOT NULL,
                    entry_date TEXT NOT NULL,
                    logged_time TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    contact_number TEXT NOT NULL,
                    acres REAL NOT NULL,
                    village TEXT NOT NULL,
                    mandal TEXT NOT NULL,
                    district TEXT NOT NULL,
                    remarks TEXT NOT NULL DEFAULT 'Not interested'
                );
                """
            )
            salt = b"test-salt"
            password_hash = data_store._password_digest("safe-test-password", salt)
            connection.execute(
                """INSERT INTO users
                   (id, username, employee_name, password_salt, password_hash,
                    role, created_at)
                   VALUES (1, ?, 'Administrator', ?, ?, 'admin', '2026-01-01')""",
                (data_store.SUPER_ADMIN_USERNAME, salt, password_hash),
            )
            connection.execute(
                """INSERT INTO users
                   (id, username, employee_name, password_salt, password_hash,
                    role, created_at, created_by_user_id)
                   VALUES (2, '600321', 'Legacy SO', ?, ?, 'user', '2026-01-01', 1)""",
                (salt, password_hash),
            )
            connection.execute(
                """INSERT INTO records
                   (id, user_id, admin_owner_id, employee_name, entry_date,
                    logged_time, customer_name, contact_number, acres, village,
                    mandal, district)
                   VALUES (1, 2, 1, 'Legacy SO', '2026-01-01', '09:00:00',
                    'Legacy customer', '9876543210', 1, 'Village', 'Mandal', 'District')"""
            )
            connection.commit()

        with patch.object(data_store, "DATABASE_PATH", legacy_path):
            self.assertTrue(data_store.initialize_database())
            with data_store._connection() as connection:
                employee = connection.execute(
                    "SELECT employee_tag, phone_number FROM users WHERE id = 2"
                ).fetchone()
                record = connection.execute(
                    "SELECT stage, loan_amount FROM records WHERE id = 1"
                ).fetchone()
        self.assertEqual(employee["employee_tag"], "SO")
        self.assertIsNone(employee["phone_number"])
        self.assertEqual(record["stage"], "Yet to login")
        self.assertEqual(record["loan_amount"], 0)


if __name__ == "__main__":
    unittest.main()
