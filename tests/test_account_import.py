from account_import import parse_account_list


def test_parse_account_list_accepts_pipe_format_and_servers():
    entries, issues = parse_account_list(
        "acc1 | pass1 | 1\n"
        "acc2|pass2|2\n"
        "acc3 | pass3 | 3\n"
    )

    assert issues == ()
    assert [
        (
            item.username,
            item.password,
            item.server,
        )
        for item in entries
    ] == [
        ("acc1", "pass1", "van_lang"),
        ("acc2", "pass2", "au_lac"),
        ("acc3", "pass3", "server_3"),
    ]


def test_parse_account_list_reports_bad_and_duplicate_lines():
    entries, issues = parse_account_list(
        "existing|p|1\n"
        "new|p|9\n"
        "new2||1\n"
        "ok|pw|2\n"
        "ok|pw2|2\n",
        existing_usernames=("existing",),
    )

    assert [
        item.username
        for item in entries
    ] == ["ok"]

    messages = [
        issue.message
        for issue in issues
    ]

    assert any(
        "đã tồn tại"
        in message
        for message in messages
    )
    assert any(
        "Server phải"
        in message
        for message in messages
    )
    assert any(
        "Thiếu mật khẩu"
        in message
        for message in messages
    )
    assert any(
        "bị lặp"
        in message
        for message in messages
    )


def test_parse_exact_two_line_van_xuan_input():
    entries, issues = parse_account_list(
        "dyplvrrg2|123123|3\n"
        "dyp1ho1v3|123123|3\n"
    )

    assert issues == ()
    assert [
        (
            item.username,
            item.password,
            item.server,
        )
        for item in entries
    ] == [
        (
            "dyplvrrg2",
            "123123",
            "server_3",
        ),
        (
            "dyp1ho1v3",
            "123123",
            "server_3",
        ),
    ]
