# Subnet allocation for 10.20.0.0/16

The subnets are allocated from largest to smallest, each one immediately after
the previous allocation. For a requirement of `n` hosts we need the smallest
prefix with `2^(32-prefix) - 2 >= n` usable addresses.

## Working

| Subnet | Need | Smallest fitting block | Host bits | Block size |
|---|---:|---|---:|---:|
| app | 1000 | /22 because `2^10 - 2 = 1022 >= 1000` (a /23 gives only 510) | 10 | 1024 |
| public | 250 | /24 because `2^8 - 2 = 254 >= 250` | 8 | 256 |
| data | 100 | /25 because `2^7 - 2 = 126 >= 100` | 7 | 128 |
| management | 20 | /27 because `2^5 - 2 = 30 >= 20` | 5 | 32 |

Starting at 10.20.0.0 and placing each block directly after the previous one:

- app: 10.20.0.0/22 covers 10.20.0.0 - 10.20.3.255, so the next free address is 10.20.4.0.
- public: 10.20.4.0/24 covers 10.20.4.0 - 10.20.4.255, so the next free address is 10.20.5.0.
- data: 10.20.5.0/25 covers 10.20.5.0 - 10.20.5.127, so the next free address is 10.20.5.128.
- management: 10.20.5.128/27 covers 10.20.5.128 - 10.20.5.159.

The remainder 10.20.5.160 - 10.20.255.255 is unallocated.

## Subnet table

| Subnet | Network address | Prefix | Usable host range | Broadcast | Usable hosts |
|---|---|---:|---|---|---:|
| app | 10.20.0.0 | /22 | 10.20.0.1 - 10.20.3.254 | 10.20.3.255 | 1022 |
| public | 10.20.4.0 | /24 | 10.20.4.1 - 10.20.4.254 | 10.20.4.255 | 254 |
| data | 10.20.5.0 | /25 | 10.20.5.1 - 10.20.5.126 | 10.20.5.127 | 126 |
| management | 10.20.5.128 | /27 | 10.20.5.129 - 10.20.5.158 | 10.20.5.159 | 30 |

## NACL design (public subnet)

`network/nacl.json` is the NACL for the public subnet, where the load balancer
lives. The app subnet is `10.20.0.0/22` and the management subnet is
`10.20.5.128/27`. Client-side ephemeral ports are 1024-65535.

Inbound, in evaluation order:

1. **100 deny all from 198.51.100.0/24.** This is a protocol `all` rule, so it
   matches every port, including 80 and 443.
2. **110 allow TCP 80 from anywhere.**
3. **120 allow TCP 443 from anywhere.**
4. **130 allow TCP 22 only from 10.20.5.128/27.**
5. **140 allow TCP 1024-65535 from 10.20.0.0/22.** These are the replies from
   the app subnet: the destination port on the load balancer is the ephemeral
   client-side port, not 8000.
6. No other inbound rule matches, so the default is deny.

Outbound:

1. **100 allow TCP 8000 to 10.20.0.0/22** (load balancer to the app subnet).
2. **110 allow TCP 1024-65535 to anywhere** (replies from the load balancer
   back to the clients' ephemeral ports).
3. Everything else falls through to the implicit deny.

## Why stateless NACLs need reply rules

A network ACL is stateless: it has no connection table and does not know that a
packet belongs to a connection that was allowed earlier. Each direction is
evaluated independently, so the return traffic of an allowed connection is not
implicitly allowed. If the public subnet allows inbound TCP 443 from a client,
the load balancer's reply leaves the public subnet towards that client's
ephemeral port (1024-65535), and that packet must match a separate outbound
rule. Likewise, the app's reply to a load-balancer request enters the public
subnet on an ephemeral destination port and needs inbound rule 140. Without
those explicit reply rules, the connection would establish in one direction and
then black-hole the responses.

## Why rule order matters for 198.51.100.0/24

NACL rules are evaluated from the lowest rule number upwards, and the first
match wins. The abusive range must therefore have a lower-numbered deny rule
than the general web allow rules. If "allow TCP 443 from 0.0.0.0/0" were rule
100 and the deny were rule 200, a packet from 198.51.100.9 to port 443 would
match the allow first and would never reach the deny. Putting the deny at rule
100 makes the block effective even on web ports.

## Validation

```console
$ python tools/nacl_sim.py network/nacl.json network/packets_sample.jsonl --check
```

The recorded output is in `results/nacl_check.txt`.
