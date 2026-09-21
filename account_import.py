"""Account-list parsing for account-centric profile management."""

from __future__ import annotations

from dataclasses import dataclass

from profile_credentials import (
    LoginCredentials,
    SERVER_NUMBERS,
)


@dataclass(frozen=True)
class AccountImportEntry:
    username: str
    password: str
    server: str
    line_number: int

    def credentials(self) -> LoginCredentials:
        return LoginCredentials(
            username=self.username,
            password=self.password,
            server=self.server,
        ).validate()


@dataclass(frozen=True)
class AccountImportIssue:
    line_number: int
    message: str


def parse_account_list(
    text: str,
    *,
    existing_usernames=(),
):
    """
    Parse lines in the form:
        username | password | server_number

    Empty lines are ignored. Server number accepts 1, 2 or 3.
    Password may contain spaces but not the pipe separator.
    """
    existing = {
        str(value).strip().casefold()
        for value in existing_usernames
        if str(value).strip()
    }

    entries = []
    issues = []
    seen = set()

    for line_number, raw in enumerate(
        (text or "").splitlines(),
        start=1,
    ):
        if not raw.strip():
            continue

        parts = [
            part.strip()
            for part in raw.split("|")
        ]

        if len(parts) != 3:
            issues.append(
                AccountImportIssue(
                    line_number,
                    "Định dạng phải là tài khoản | mật khẩu | sv",
                )
            )
            continue

        username, password, server_number = parts

        if not username:
            issues.append(
                AccountImportIssue(
                    line_number,
                    "Thiếu tài khoản",
                )
            )
            continue

        if not password:
            issues.append(
                AccountImportIssue(
                    line_number,
                    "Thiếu mật khẩu",
                )
            )
            continue

        server = SERVER_NUMBERS.get(
            server_number
        )

        if server is None:
            issues.append(
                AccountImportIssue(
                    line_number,
                    "Server phải là 1, 2 hoặc 3",
                )
            )
            continue

        key = username.casefold()

        if key in existing:
            issues.append(
                AccountImportIssue(
                    line_number,
                    f"Tài khoản đã tồn tại: {username}",
                )
            )
            continue

        if key in seen:
            issues.append(
                AccountImportIssue(
                    line_number,
                    f"Tài khoản bị lặp trong danh sách: {username}",
                )
            )
            continue

        seen.add(key)
        entries.append(
            AccountImportEntry(
                username=username,
                password=password,
                server=server,
                line_number=line_number,
            )
        )

    return tuple(entries), tuple(issues)
