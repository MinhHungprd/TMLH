from types import SimpleNamespace

import pytest

from proxy_manager import (
    Socks5Proxy,
    assign_profile_proxies,
    build_proxifyre_config,
    parse_proxy_line,
    proxy_for_profile_index,
    proxy_for_test_profile_index,
)


def profile(index):
    return SimpleNamespace(
        profile_id=f"P{index + 1}",
        game_path=f"C:/TMLH/Game/profiles/P{index + 1}",
    )


def proxy(host):
    return Socks5Proxy(
        host=host,
        port=1080,
        username="user",
        password="pass",
    )


def test_parse_proxy_ip_port_user_pass():
    item = parse_proxy_line(
        "1.2.3.4:1080:abc:def"
    )

    assert item.host == "1.2.3.4"
    assert item.port == 1080
    assert item.username == "abc"
    assert item.password == "def"


def test_parse_proxy_allows_colon_inside_password():
    item = parse_proxy_line(
        "1.2.3.4:1080:user:pa:ss"
    )

    assert item.password == "pa:ss"


def test_first_30_profiles_are_direct():
    assert all(
        proxy_for_profile_index(
            index,
            3,
        )
        is None
        for index in range(30)
    )


def test_proxy_groups_are_30_profiles_each():
    assert proxy_for_profile_index(30, 3) == 0
    assert proxy_for_profile_index(59, 3) == 0
    assert proxy_for_profile_index(60, 3) == 1
    assert proxy_for_profile_index(89, 3) == 1
    assert proxy_for_profile_index(90, 3) == 2


def test_one_proxy_is_reused_for_every_profile_after_30():
    assert proxy_for_profile_index(30, 1) == 0
    assert proxy_for_profile_index(60, 1) == 0
    assert proxy_for_profile_index(300, 1) == 0


def test_last_proxy_is_reused_when_profiles_exceed_proxy_count():
    assert proxy_for_profile_index(90, 2) == 1
    assert proxy_for_profile_index(200, 2) == 1


def test_assignment_uses_profile_list_order():
    profiles = [
        profile(index)
        for index in range(65)
    ]
    proxies = [
        proxy("10.0.0.1"),
        proxy("10.0.0.2"),
    ]

    assigned = assign_profile_proxies(
        profiles,
        proxies,
    )

    assert assigned["P30"] is None
    assert assigned["P31"] == 0
    assert assigned["P60"] == 0
    assert assigned["P61"] == 1
    assert assigned["P65"] == 1


def test_proxifyre_config_contains_only_profiles_after_first_30():
    profiles = [
        profile(index)
        for index in range(35)
    ]

    config = build_proxifyre_config(
        profiles,
        [proxy("10.0.0.1")],
    )

    assert config["logLevel"] == "Error"
    assert config["bypassLan"] is True
    assert len(config["proxies"]) == 1

    rule = config["proxies"][0]

    assert rule["socks5ProxyEndpoint"] == "10.0.0.1:1080"
    assert rule["username"] == "user"
    assert rule["password"] == "pass"
    assert rule["supportedProtocols"] == ["TCP"]

    assert len(rule["appNames"]) == 5
    assert all(
        "P31" in path
        or "P32" in path
        or "P33" in path
        or "P34" in path
        or "P35" in path
        for path in rule["appNames"]
    )


def test_invalid_proxy_format_is_rejected():
    with pytest.raises(
        ValueError,
        match="ip:port:user:pass",
    ):
        parse_proxy_line(
            "1.2.3.4:1080"
        )



def test_small_batch_test_mode_routes_second_profile_through_first_proxy():
    assert proxy_for_test_profile_index(
        0,
        2,
    ) is None
    assert proxy_for_test_profile_index(
        1,
        2,
    ) == 0
    assert proxy_for_test_profile_index(
        2,
        2,
    ) == 1
    assert proxy_for_test_profile_index(
        3,
        2,
    ) == 1


def test_two_profiles_can_build_real_proxy_route_in_test_mode():
    profiles = [
        profile(0),
        profile(1),
    ]

    config = build_proxifyre_config(
        profiles,
        [proxy("10.0.0.1")],
        test_mode=True,
    )

    assert len(
        config["proxies"]
    ) == 1

    rule = config["proxies"][0]

    assert rule[
        "socks5ProxyEndpoint"
    ] == "10.0.0.1:1080"

    assert len(
        rule["appNames"]
    ) == 1

    assert "P2" in rule[
        "appNames"
    ][0]

    assert "P1" not in rule[
        "appNames"
    ][0]


def test_two_profiles_stay_direct_in_normal_mode():
    profiles = [
        profile(0),
        profile(1),
    ]

    config = build_proxifyre_config(
        profiles,
        [proxy("10.0.0.1")],
    )

    assert config[
        "proxies"
    ] == []
