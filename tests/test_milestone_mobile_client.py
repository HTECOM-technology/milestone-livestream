import unittest

from app.milestone.auth import (
    LOGIN_TYPE_ACTIVE_DIRECTORY,
    LOGIN_TYPE_BASIC,
    build_login_username,
    normalize_login_type,
)
from app.milestone.mobile_client import extract_params

PROCESSING_BLOCK = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Communication xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<ConnectionId /><Command SequenceId=\"1\"><Type>Processing</Type>"
    "<Name>Connect</Name><InputParams /><OutputParams /><Result>OK</Result>"
    "</Command></Communication>\r\n\r\n"
)

CONNECT_RESPONSE_BLOCK = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Communication xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<ConnectionId>abc-123</ConnectionId><Command SequenceId=\"1\">"
    "<Type>Response</Type><Name>Connect</Name><InputParams />"
    '<OutputParams><Param Name="PublicKey" Value="QUJD" /></OutputParams>'
    "<Result>OK</Result></Command></Communication>\r\n\r\n"
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


class ExtractParamsTests(unittest.TestCase):
    def test_single_response(self) -> None:
        parsed = extract_params(CONNECT_RESPONSE_BLOCK)
        self.assertEqual(parsed["connection_id"], "abc-123")
        self.assertEqual(parsed["params"]["PublicKey"], "QUJD")

    def test_processing_blocks_before_response_are_skipped(self) -> None:
        parsed = extract_params(PROCESSING_BLOCK * 5 + CONNECT_RESPONSE_BLOCK)
        self.assertEqual(parsed["connection_id"], "abc-123")
        self.assertEqual(parsed["params"]["PublicKey"], "QUJD")
        self.assertEqual(parsed["result"], "OK")

    def test_only_processing_blocks_has_no_connection_id(self) -> None:
        parsed = extract_params(PROCESSING_BLOCK * 3)
        self.assertIsNone(parsed["connection_id"])


if __name__ == "__main__":
    unittest.main()
