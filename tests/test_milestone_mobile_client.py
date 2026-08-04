import unittest

from app.milestone.auth import (
    LOGIN_TYPE_ACTIVE_DIRECTORY,
    LOGIN_TYPE_BASIC,
    build_login_username,
    normalize_login_type,
)


class LoginIdentityTests(unittest.TestCase):
    def test_basic_user_is_not_prefixed_with_domain(self) -> None:
        self.assertEqual(
            build_login_username(LOGIN_TYPE_BASIC, "VMS-ITS", "camera-reader"),
            "camera-reader",
        )

    def test_active_directory_user_is_prefixed_with_domain(self) -> None:
        self.assertEqual(
            build_login_username(
                LOGIN_TYPE_ACTIVE_DIRECTORY, "VMS-ITS", "administrator"
            ),
            r"VMS-ITS\administrator",
        )

    def test_qualified_active_directory_username_is_preserved(self) -> None:
        self.assertEqual(
            build_login_username(
                LOGIN_TYPE_ACTIVE_DIRECTORY, "OTHER", r"VMS-ITS\administrator"
            ),
            r"VMS-ITS\administrator",
        )

    def test_login_type_is_normalized(self) -> None:
        self.assertEqual(normalize_login_type("basic"), LOGIN_TYPE_BASIC)
        self.assertEqual(
            normalize_login_type("active_directory"),
            LOGIN_TYPE_ACTIVE_DIRECTORY,
        )

    def test_invalid_login_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "MILESTONE_LOGIN_TYPE"):
            normalize_login_type("unknown")

if __name__ == "__main__":
    unittest.main()
