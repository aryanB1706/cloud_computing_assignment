from __future__ import annotations

import ipaddress
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_plan():
    return json.loads((ROOT / "network" / "subnets.json").read_text(encoding="utf-8"))


def test_required_host_counts_and_prefixes():
    plan = load_plan()
    by_name = {s["name"]: s for s in plan["subnets"]}
    expected = {"app": (1000, 22), "public": (250, 24), "data": (100, 25), "management": (20, 27)}
    for name, (hosts, prefix) in expected.items():
        subnet = by_name[name]
        assert subnet["hosts_needed"] == hosts
        assert subnet["prefix"] == prefix
        net = ipaddress.ip_network(subnet["cidr"])
        assert net.prefixlen == prefix
        assert net.num_addresses - 2 >= hosts


def test_table_values_match_ipaddress():
    plan = load_plan()
    for subnet in plan["subnets"]:
        net = ipaddress.ip_network(subnet["cidr"])
        hosts = list(net.hosts())
        assert str(net.network_address) == subnet["network"]
        assert str(net.broadcast_address) == subnet["broadcast"]
        assert str(hosts[0]) == subnet["first_usable"]
        assert str(hosts[-1]) == subnet["last_usable"]
        assert len(hosts) == subnet["usable_hosts"] == net.num_addresses - 2


def test_blocks_are_contiguous_and_do_not_overlap():
    plan = load_plan()
    networks = [ipaddress.ip_network(s["cidr"]) for s in plan["subnets"]]
    for current, following in zip(networks, networks[1:]):
        assert int(current.broadcast_address) + 1 == int(following.network_address)
        assert not current.overlaps(following)
    assert int(networks[0].network_address) == int(ipaddress.ip_network(plan["block"]).network_address)


def test_app_and_management_cidrs_used_by_nacl():
    plan = load_plan()
    by_name = {s["name"]: s for s in plan["subnets"]}
    assert by_name["app"]["cidr"] == "10.20.0.0/22"
    assert by_name["management"]["cidr"] == "10.20.5.128/27"
