from __future__ import annotations

import json
from pathlib import Path

import nacl_sim

ROOT = Path(__file__).resolve().parents[1]


def rules():
    return nacl_sim.load_rules(str(ROOT / "network" / "nacl.json"))


def packet(direction, port, address, protocol="tcp"):
    return {"direction": direction, "port": port, "address": address, "protocol": protocol}


def test_official_sample_packets_all_correct():
    loaded = rules()
    packets = [
        json.loads(line)
        for line in (ROOT / "network" / "packets_sample.jsonl").read_text().splitlines()
        if line.strip()
    ]
    correct = 0
    for p in packets:
        action, _rule = nacl_sim.decide(loaded, p)
        correct += action == p["expected"]
    assert correct == len(packets) == 6


def test_abusive_range_is_denied_even_on_web_ports():
    loaded = rules()
    for port in (80, 443, 22, 8000, 33000):
        assert nacl_sim.decide(loaded, packet("inbound", port, "198.51.100.9"))[0] == "deny"


def test_ssh_only_from_management_subnet():
    loaded = rules()
    assert nacl_sim.decide(loaded, packet("inbound", 22, "10.20.5.129"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("inbound", 22, "10.20.5.158"))[0] == "allow"
    # CIDR matching is address-based; .159 is broadcast for the /27 but the
    # NACL still sees it inside 10.20.5.128/27.
    assert nacl_sim.decide(loaded, packet("inbound", 22, "10.20.5.159"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("inbound", 22, "10.20.5.128"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("inbound", 22, "10.20.5.160"))[0] == "deny"
    assert nacl_sim.decide(loaded, packet("inbound", 22, "192.0.2.10"))[0] == "deny"


def test_app_replies_use_ephemeral_ports():
    loaded = rules()
    assert nacl_sim.decide(loaded, packet("inbound", 33000, "10.20.2.10"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("inbound", 1024, "10.20.3.254"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("inbound", 1023, "10.20.2.10"))[0] == "deny"
    assert nacl_sim.decide(loaded, packet("inbound", 33000, "10.20.4.10"))[0] == "deny"


def test_outbound_only_app_8000_and_client_replies():
    loaded = rules()
    assert nacl_sim.decide(loaded, packet("outbound", 8000, "10.20.0.1"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("outbound", 8000, "10.20.3.255"))[0] == "allow"
    # 8000 also falls in the client ephemeral range, so it is allowed by the
    # reply rule regardless of destination; this is an inherent overlap.
    assert nacl_sim.decide(loaded, packet("outbound", 8000, "10.20.4.1"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("outbound", 50000, "203.0.113.9"))[0] == "allow"
    assert nacl_sim.decide(loaded, packet("outbound", 80, "203.0.113.9"))[0] == "deny"
    assert nacl_sim.decide(loaded, packet("outbound", 443, "203.0.113.9"))[0] == "deny"
    assert nacl_sim.decide(loaded, packet("outbound", 50000, "203.0.113.9", protocol="udp"))[0] == "deny"


def test_protocol_must_match():
    loaded = rules()
    assert nacl_sim.decide(loaded, packet("inbound", 443, "192.0.2.5", protocol="udp"))[0] == "deny"
