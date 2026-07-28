"""Shared wording helpers behind splent login / whoami / logout."""

from splent_cli.commands.marketplace.marketplace_auth_utils import retry_hint


class TestRetryHint:
    """Retry-After is either a number of seconds or an HTTP date.

    Whatever arrives, the developer gets an instruction rather than a raw
    header, and an unparseable value is quoted instead of silently dropped.
    """

    def test_no_header_still_gives_an_instruction(self):
        assert "Wait a moment" in retry_hint(None)
        assert "Wait a moment" in retry_hint("")
        assert "Wait a moment" in retry_hint("   ")

    def test_seconds_are_reported_verbatim(self):
        assert retry_hint("45") == "Wait 45 seconds and run the command again."

    def test_one_second_is_not_pluralised(self):
        assert retry_hint("1") == "Wait a second and run the command again."
        assert retry_hint("0") == "Wait a second and run the command again."

    def test_long_waits_are_rounded_to_minutes(self):
        """"Wait 3600 seconds" is a number a human has to do arithmetic on."""
        assert retry_hint("3600") == "Wait about 60 minutes and run the command again."
        assert retry_hint("120") == "Wait about 2 minutes and run the command again."

    def test_an_http_date_is_quoted_rather_than_dropped(self):
        hint = retry_hint("Wed, 21 Oct 2026 07:28:00 GMT")
        assert "Wed, 21 Oct 2026 07:28:00 GMT" in hint

    def test_the_hint_never_ends_up_empty(self):
        for value in (None, "", "0", "5", "600", "nonsense"):
            assert retry_hint(value).strip()
