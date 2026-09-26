from unittest.mock import patch

import profile_auth as auth


def _saved_identity(
    username,
    server_id,
):
    return [
        {
            "name": "UsernameThienMenh_main",
            "type": 1,
            "data": {
                "kind": "json",
                "value": username,
            },
        },
        {
            "name": "ID_SERVER_INT_main",
            "type": 4,
            "data": {
                "kind": "json",
                "value": server_id,
            },
        },
        {
            "name": "token_login_main",
            "type": 1,
            "data": {
                "kind": "json",
                "value": "secret-token",
            },
        },
    ]


def test_guarded_cleanup_clears_only_matching_profile():
    values = _saved_identity(
        "account-a",
        1,
    )
    identity = (
        auth._identity_from_saved_values(
            values
        )
    )

    with patch.object(
        auth,
        "_load_profile_auth_values",
        return_value=values,
    ), patch.object(
        auth,
        "_read_current_auth_identity",
        return_value=identity,
    ), patch.object(
        auth,
        "clear_current_auth_values",
    ) as clear:
        assert (
            auth.clear_profile_auth_if_current(
                "profile-a"
            )
            is True
        )

    clear.assert_called_once_with()


def test_guarded_cleanup_does_not_delete_another_profiles_auth():
    values_a = _saved_identity(
        "account-a",
        1,
    )
    values_b = _saved_identity(
        "account-b",
        2,
    )

    identity_b = (
        auth._identity_from_saved_values(
            values_b
        )
    )

    with patch.object(
        auth,
        "_load_profile_auth_values",
        return_value=values_a,
    ), patch.object(
        auth,
        "_read_current_auth_identity",
        return_value=identity_b,
    ), patch.object(
        auth,
        "clear_current_auth_values",
    ) as clear:
        assert (
            auth.clear_profile_auth_if_current(
                "profile-a"
            )
            is False
        )

    clear.assert_not_called()
